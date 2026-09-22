"""URL import discovery provider — turns a list of URLs directly into
candidates, no external API needed. First provider implemented since
it's what an MVP CLI workflow needs: paste in known store URLs to seed
the pipeline. See scraper_module_contracts.md S1.
"""

from __future__ import annotations

from typing import ClassVar, List, Set

from scraper.domain.types import CandidateDiscovery, DiscoveryCriteria


class UrlImportProvider:
    name = "url_import"
    supported_criteria: ClassVar[Set[str]] = {"urls", "limit"}

    def discover(self, criteria: DiscoveryCriteria) -> List[CandidateDiscovery]:
        urls = criteria.urls[: criteria.limit] if criteria.limit else list(criteria.urls)
        return [CandidateDiscovery(raw_url=url, source=self.name, name_hint=None, discovery_metadata={}) for url in urls]
