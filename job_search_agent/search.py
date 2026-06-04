from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - fallback for older environments
    from duckduckgo_search import DDGS

from .config import SETTINGS
from .llm import get_reasoning_llm
from .models import SearchCandidate
from .logging_config import logger
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


RECRUITER_TERMS = (
    "recruiter",
    "recruiting",
    "staffing",
    "talent acquisition",
    "talent source",
    "hiring partner",
    "headhunter",
    "consulting",
    "outsourcing",
)

SCAM_TERMS = (
    "confidential",
    "stealth",
    "urgent hiring",
    "work from home and earn",
    "easy money",
    "crypto",
    "gift card",
    "telegram",
    "whatsapp",
)

DATE_KEYS = (
    "datePosted",
    "datePublished",
    "dateCreated",
    "published_time",
    "article:published_time",
    "og:updated_time",
)

JOB_URL_HINTS = (
    "/jobs/",
    "/careers/",
    "/job/",
    "/openings/",
    "/positions/",
    "/roles/",
    "/vacancies/",
)

JOB_BOARD_DOMAINS = (
    "greenhouse.io",
    "lever.co",
    "workday.com",
    "icims.com",
    "smartrecruiters.com",
    "ashbyhq.com",
)


class StrictSifter:
    def __init__(self, llm=None) -> None:
        self.llm = llm or get_reasoning_llm()

    def build_search_queries(self, target_role: str, tech_stack: str) -> list[str]:
        tech_terms = [term for term in re.split(r"[\s,/|]+", tech_stack.strip()) if term]
        core_terms = tech_terms[:3] if tech_terms else []
        core_query_terms = " ".join(core_terms) if core_terms else tech_stack.strip()
        prompts = [
            f'site:boards.greenhouse.io "{target_role}" "{core_query_terms}"',
            f'site:jobs.lever.co "{target_role}" "{core_query_terms}"',
            f'site:workday.com "{target_role}" "{core_query_terms}"',
            f'site:icims.com "{target_role}" "{core_query_terms}"',
            f'site:smartrecruiters.com "{target_role}" "{core_query_terms}"',
            f'site:ashbyhq.com "{target_role}" "{core_query_terms}"',
        ]
        for term in core_terms:
            prompts.append(f'site:boards.greenhouse.io "{target_role}" "{term}"')
            prompts.append(f'site:jobs.lever.co "{target_role}" "{term}"')
        prompts.append(f'"{target_role}" jobs careers')
        return prompts

    def search(self, target_role: str, tech_stack: str, max_results: int = 20) -> list[SearchCandidate]:
        logger.info("[StrictSifter] Searching for direct-employer jobs for role='%s' stack='%s'", target_role, tech_stack)
        results: list[SearchCandidate] = []
        seen_urls: set[str] = set()
        queries = self.build_search_queries(target_role, tech_stack)
        for query in queries:
            for candidate in self._search_query(query, max_results=max_results):
                if candidate.url in seen_urls:
                    continue
                seen_urls.add(candidate.url)
                results.append(candidate)
        return results

    def _search_query(self, query: str, max_results: int = 20) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        try:
            with DDGS() as ddgs:
                search_results = ddgs.text(query, max_results=max_results, safesearch="moderate")
                for result in search_results:
                    url = result.get("href") or result.get("url")
                    title = result.get("title") or ""
                    body = result.get("body") or ""
                    if not url:
                        continue
                    logger.debug("[StrictSifter] Raw hit: %s | %s", title, url)
                    if not self._looks_like_job_url(url, title, body):
                        if not self._looks_like_job_posting(title, body):
                            continue
                    validated = self._validate_job_link(url=url, title=title, snippet=body)
                    if validated is not None:
                        candidates.append(validated)
        except Exception as exc:
            logger.exception("[StrictSifter] DuckDuckGo search failed for query '%s'", query)
        return candidates

    def _validate_job_link(self, url: str, title: str, snippet: str) -> SearchCandidate | None:
        page = self._fetch_page(url)
        if page is None:
            return None

        company = page.get("company") or self._guess_company_from_domain(url)
        job_title = page.get("job_title") or title or self._guess_job_title(snippet)
        job_description = page.get("job_description") or snippet or title
        published_at = page.get("published_at")
        direct_employer = page.get("direct_employer", True)
        rejection_reason = self._rejection_reason(company, url, job_description, published_at, direct_employer)
        if rejection_reason:
            logger.info("[StrictSifter] Rejected %s: %s", url, rejection_reason)
            return None
        date_found = datetime.now(timezone.utc).isoformat()
        logger.info("[StrictSifter] Accepted direct employer link: %s | %s", company, job_title)
        return SearchCandidate(
            job_title=job_title.strip(),
            company=company.strip(),
            url=url,
            job_description=job_description.strip(),
            date_found=date_found,
            source="duckduckgo",
            is_direct_employer=True,
        )

    @staticmethod
    def _looks_like_job_url(url: str, title: str, snippet: str) -> bool:
        lower_url = url.lower()
        lower_title = (title or "").lower()
        lower_snippet = (snippet or "").lower()

        if any(domain in lower_url for domain in JOB_BOARD_DOMAINS):
            return True
        if any(hint in lower_url for hint in JOB_URL_HINTS):
            return True
        if any(word in lower_title for word in ("jobs", "careers", "open role", "open position", "hiring")):
            return True
        if any(word in lower_snippet for word in ("job description", "apply now", "responsibilities", "qualifications")):
            return True
        return False

    @staticmethod
    def _looks_like_job_posting(title: str, snippet: str) -> bool:
        lower_title = (title or "").lower()
        lower_snippet = (snippet or "").lower()
        job_signals = (
            "job",
            "jobs",
            "career",
            "careers",
            "hiring",
            "opening",
            "openings",
            "role",
            "position",
            "apply",
            "responsibilities",
            "qualifications",
        )
        signal_count = sum(1 for signal in job_signals if signal in lower_title or signal in lower_snippet)
        return signal_count >= 2

    def _fetch_page(self, url: str) -> dict[str, object] | None:
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            }
            session = _make_session()
            response = session.get(url, headers=headers, timeout=20)
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as http_exc:
                # Try a few pragmatic fallbacks for common job board URL patterns
                logger.warning("[StrictSifter] Warning: %s returned %s, trying fallback fetches", url, response.status_code)
                # Strip query params and retry
                parsed = urlparse(url)
                base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if base != url:
                    try:
                        r2 = session.get(base, headers=headers, timeout=15)
                        r2.raise_for_status()
                        response = r2
                    except Exception:
                        logger.debug("fallback to base URL failed for %s", base)
                # Try adding '/apply' if not present (common for lever/apply endpoints)
                if not base.lower().endswith("/apply"):
                    try_apply = base.rstrip("/") + "/apply"
                    try:
                        r3 = session.get(try_apply, headers=headers, timeout=15)
                        r3.raise_for_status()
                        response = r3
                    except Exception:
                        logger.debug("fallback to apply URL failed for %s", try_apply)
                # If still failing re-raise the original exception to be caught below
                try:
                    response.raise_for_status()
                except Exception:
                    raise http_exc
            soup = BeautifulSoup(response.text, "html.parser")
            json_ld = self._extract_json_ld(soup)
            company = self._to_text(json_ld.get("hiringOrganization") or json_ld.get("company")) or self._extract_company(soup)
            job_title = self._to_text(json_ld.get("title")) or self._extract_title(soup)
            job_description = self._to_text(json_ld.get("description")) or self._extract_description(soup)
            published_at = self._extract_date(json_ld, soup)
            direct_employer = self._is_direct_employer(company, url, soup)
            return {
                "company": company,
                "job_title": job_title,
                "job_description": job_description,
                "published_at": published_at,
                "direct_employer": direct_employer,
            }
        except Exception:
            logger.exception("[StrictSifter] Failed to inspect %s", url)
            return None

    @staticmethod
    def _extract_json_ld(soup: BeautifulSoup) -> dict[str, object]:
        scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
        for script in scripts:
            if not script.string:
                continue
            try:
                payload = json.loads(script.string)
                if isinstance(payload, dict):
                    if payload.get("@type") == "JobPosting":
                        return payload
                    if isinstance(payload.get("mainEntity"), dict) and payload["mainEntity"].get("@type") == "JobPosting":
                        return payload["mainEntity"]
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict) and item.get("@type") == "JobPosting":
                            return item
            except Exception:
                continue
        return {}

    @staticmethod
    def _extract_company(soup: BeautifulSoup) -> str:
        for selector in [
            "[data-company]",
            ".company",
            ".job-company",
            ".hiring-organization",
            "meta[property='og:site_name']",
        ]:
            element = soup.select_one(selector)
            if element:
                if element.name == "meta":
                    return element.get("content", "")
                return element.get_text(" ", strip=True)
        return ""

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        title_tag = soup.find("title")
        if title_tag and title_tag.text:
            return title_tag.text.strip()
        heading = soup.find(["h1", "h2"])
        return heading.get_text(" ", strip=True) if heading else ""

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            return meta["content"].strip()
        paragraphs = soup.find_all("p")
        text = " ".join(paragraph.get_text(" ", strip=True) for paragraph in paragraphs[:8])
        return re.sub(r"\s+", " ", text).strip()

    def _extract_date(self, json_ld: dict[str, object], soup: BeautifulSoup) -> str:
        if json_ld.get("datePosted"):
            return self._to_text(json_ld["datePosted"])
        if json_ld.get("datePublished"):
            return self._to_text(json_ld["datePublished"])
        for key in DATE_KEYS:
            meta = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if meta and meta.get("content"):
                return meta["content"].strip()
        return ""

    def _is_direct_employer(self, company: str, url: str, soup: BeautifulSoup) -> bool:
        company_text = self._to_text(company).lower()
        domain = urlparse(url).netloc.lower()
        if any(term in company_text for term in RECRUITER_TERMS):
            return False
        if any(term in domain for term in ["greenhouse", "lever", "workday", "icims", "smartrecruiters"]):
            return True
        text = soup.get_text(" ", strip=True).lower()
        if any(term in text for term in RECRUITER_TERMS):
            return False
        return True

    def _rejection_reason(self, company: str, url: str, job_description: str, published_at: str, direct_employer: bool) -> str:
        if not direct_employer:
            return "Looks like a third-party recruiter or staffing intermediary."
        if not published_at:
            if any(domain in url.lower() for domain in JOB_BOARD_DOMAINS) and len(job_description.strip()) >= 60:
                return ""
            return "No verifiable posting timestamp found."
        published_dt = self._parse_datetime(published_at)
        if published_dt is None:
            if any(domain in url.lower() for domain in JOB_BOARD_DOMAINS) and len(job_description.strip()) >= 60:
                return ""
            return "Posting timestamp could not be parsed."
        age_days = (datetime.now(timezone.utc) - published_dt).days
        if age_days > SETTINGS.max_job_age_days:
            return f"Posting is {age_days} days old, older than the {SETTINGS.max_job_age_days}-day cutoff."
        lower_company = company.lower().strip()
        if not lower_company or lower_company in {"confidential", "stealth", "anonymous", "unknown"}:
            return "Company identity is too vague to trust."
        if any(term in f"{company} {job_description}".lower() for term in SCAM_TERMS):
            return "Contains scam or low-trust language."
        if len(job_description.strip()) < 60:
            return "Job description is too thin to verify as a real posting."
        return ""

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _guess_company_from_domain(url: str) -> str:
        domain = urlparse(url).netloc.lower()
        parts = [part for part in re.split(r"[.-]", domain) if part and part not in {"www", "jobs", "careers"}]
        if not parts:
            return "Unknown"
        return parts[0].capitalize()

    @staticmethod
    def _to_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("name", "title", "text", "content", "value"):
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
            return ""
        if isinstance(value, list):
            parts = [StrictSifter._to_text(item) for item in value]
            return " ".join(part for part in parts if part).strip()
        return str(value).strip()

    @staticmethod
    def _guess_job_title(snippet: str) -> str:
        match = re.search(r"([A-Z][A-Za-z0-9\-\+/ ]{10,80}?)(?:\s+job|\s+role|\s+position)", snippet)
        return match.group(1).strip() if match else "Open Role"
