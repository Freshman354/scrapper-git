"""Tests for scraper/extraction/.

Extraction is pure HTML-in/fields-out, so these are ordinary unit tests
against real BeautifulSoup/lxml parsing of literal HTML fixtures — no
mocking needed since the parser itself is the real thing.
"""

from datetime import datetime, timezone

from bs4 import BeautifulSoup

from scraper.domain.types import ExtractedField, FetchedPage
from scraper.extraction import business_info, contacts, extract, products, socials, structured_data

FETCHED_AT = datetime.now(timezone.utc)


def _page(url, html):
    return FetchedPage(url=url, page_type="homepage", fetched_via="http", http_status_code=200,
                        content_hash="hash", raw_html=html, from_cache=False, fetched_at=FETCHED_AT)


def _soup(html):
    return BeautifulSoup(html, "lxml")


# --- structured_data.py -------------------------------------------------------

JSON_LD_ORG_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "Example Store",
  "description": "The finest widgets.",
  "email": "hello@example.com",
  "telephone": "+1-555-123-4567",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "123 Main St",
    "addressLocality": "Austin",
    "addressRegion": "TX",
    "addressCountry": "US",
    "postalCode": "78701"
  },
  "sameAs": ["https://instagram.com/examplestore", "https://facebook.com/examplestore"]
}
</script>
</head><body></body></html>
"""


def test_structured_data_extracts_json_ld_organization_fields():
    fields = structured_data.extract_page(_soup(JSON_LD_ORG_HTML), "https://example.com/")
    by_name = {f.field_name: f.field_value for f in fields}
    assert by_name["name"] == "Example Store"
    assert by_name["description"] == "The finest widgets."
    assert by_name["email"] == "hello@example.com"
    assert by_name["telephone"] == "+1-555-123-4567"
    assert by_name["street_address"] == "123 Main St"
    assert by_name["city"] == "Austin"
    assert by_name["state"] == "TX"
    assert by_name["country"] == "US"
    assert by_name["postal_code"] == "78701"
    assert all(f.extraction_method == "json-ld" for f in fields if f.field_name != "sameAs" or True)


def test_structured_data_extracts_same_as_as_multiple_fields():
    fields = structured_data.extract_page(_soup(JSON_LD_ORG_HTML), "https://example.com/")
    same_as_values = {f.field_value for f in fields if f.field_name == "sameAs"}
    assert same_as_values == {"https://instagram.com/examplestore", "https://facebook.com/examplestore"}


def test_structured_data_handles_malformed_json_ld_gracefully():
    html = '<html><head><script type="application/ld+json">{not valid json,,,</script></head></html>'
    fields = structured_data.extract_page(_soup(html), "https://example.com/")
    assert fields == []  # no crash, just nothing extracted from that block


def test_structured_data_extracts_product_type():
    html = """
    <script type="application/ld+json">
    {"@type": "Product", "name": "Blue Widget", "category": "Widgets"}
    </script>
    """
    fields = structured_data.extract_page(_soup(html), "https://example.com/products/blue-widget")
    by_name = {f.field_name: f.field_value for f in fields}
    assert by_name["product_name"] == "Blue Widget"
    assert by_name["category"] == "Widgets"


def test_structured_data_falls_back_to_opengraph_and_meta():
    html = """
    <html><head>
      <meta property="og:title" content="OG Store Name">
      <meta name="description" content="Meta description here">
      <meta name="generator" content="Shopify">
      <title>Title Tag Store</title>
    </head></html>
    """
    fields = structured_data.extract_page(_soup(html), "https://example.com/")
    methods = {(f.field_name, f.extraction_method): f.field_value for f in fields}
    assert methods[("name", "og-tag")] == "OG Store Name"
    assert methods[("description", "meta-tag")] == "Meta description here"
    assert methods[("generator", "meta-tag")] == "Shopify"
    assert methods[("name", "title-tag")] == "Title Tag Store"


def test_structured_data_json_ld_confidence_higher_than_meta():
    fields = structured_data.extract_page(_soup(JSON_LD_ORG_HTML), "https://example.com/")
    name_field = next(f for f in fields if f.field_name == "name")
    assert name_field.confidence == structured_data.JSON_LD_CONFIDENCE
    assert structured_data.JSON_LD_CONFIDENCE > structured_data.OPENGRAPH_CONFIDENCE > structured_data.META_TAG_CONFIDENCE


# --- business_info.py -----------------------------------------------------------

def test_business_info_passes_through_business_fields_only():
    structured_fields = [
        ExtractedField("name", "Example Store", "https://x.com/", "json-ld", 0.9),
        ExtractedField("email", "hi@x.com", "https://x.com/", "json-ld", 0.9),  # not a business field
        ExtractedField("city", "Austin", "https://x.com/", "json-ld", 0.9),
    ]
    result = business_info.extract_page(_soup("<html></html>"), "https://x.com/", structured_fields)
    field_names = {f.field_name for f in result}
    assert field_names == {"name", "city"}


# --- contacts.py -----------------------------------------------------------------

def test_contacts_extracts_mailto_and_tel_links():
    html = '<html><body><a href="mailto:hello@example.com">Email</a><a href="tel:+15551234567">Call</a></body></html>'
    fields = contacts.extract_page(_soup(html), "https://example.com/contact", [])
    by_name = {f.field_name: f.field_value for f in fields}
    assert by_name["email"] == "hello@example.com"
    assert by_name["phone"] == "+15551234567"


def test_contacts_dedupes_mailto_against_structured_data():
    structured_fields = [ExtractedField("email", "hello@example.com", "https://x.com/", "json-ld", 0.9)]
    html = '<a href="mailto:hello@example.com">Email</a>'
    fields = contacts.extract_page(_soup(html), "https://x.com/", structured_fields)
    emails = [f for f in fields if f.field_name == "email"]
    assert len(emails) == 1
    assert emails[0].extraction_method == "json-ld"  # structured data wins, mailto duplicate dropped


def test_contacts_regex_fallback_finds_plain_text_email():
    html = "<p>Reach us at hello@example.com anytime.</p>"
    fields = contacts.extract_page(_soup(html), "https://x.com/", [])
    assert len(fields) == 1
    assert fields[0].field_value == "hello@example.com"
    assert fields[0].extraction_method == "regex"
    assert fields[0].confidence == contacts.REGEX_EMAIL_CONFIDENCE


def test_contacts_no_data_returns_empty_list():
    fields = contacts.extract_page(_soup("<html><body>Nothing here.</body></html>"), "https://x.com/", [])
    assert fields == []


# --- socials.py --------------------------------------------------------------------

def test_socials_extracts_known_platform_links():
    html = """
    <a href="https://instagram.com/examplestore">IG</a>
    <a href="https://www.facebook.com/examplestore">FB</a>
    <a href="https://random-blog.com/">Not social</a>
    """
    fields = socials.extract_page(_soup(html), "https://x.com/", [])
    by_platform = {f.field_name: f.field_value for f in fields}
    assert by_platform["instagram"] == "https://instagram.com/examplestore"
    assert by_platform["facebook"] == "https://www.facebook.com/examplestore"
    assert "random-blog" not in str(by_platform)


def test_socials_twitter_domain_maps_to_x_platform():
    html = '<a href="https://twitter.com/examplestore">Twitter</a>'
    fields = socials.extract_page(_soup(html), "https://x.com/", [])
    assert fields[0].field_name == "x"


def test_socials_dedupes_json_ld_sameas_against_link():
    structured_fields = [ExtractedField("sameAs", "https://instagram.com/examplestore", "https://x.com/", "json-ld", 0.9)]
    html = '<a href="https://instagram.com/examplestore">IG</a>'
    fields = socials.extract_page(_soup(html), "https://x.com/", structured_fields)
    instagram_fields = [f for f in fields if f.field_name == "instagram"]
    assert len(instagram_fields) == 1
    assert instagram_fields[0].extraction_method == "json-ld"


# --- products.py -------------------------------------------------------------------

def test_products_passes_through_product_fields():
    structured_fields = [
        ExtractedField("product_name", "Blue Widget", "https://x.com/products/blue", "json-ld", 0.9),
        ExtractedField("name", "Example Store", "https://x.com/", "json-ld", 0.9),  # not a product field
    ]
    result = products.extract_page(_soup("<html></html>"), "https://x.com/products/blue", structured_fields)
    assert len(result) == 1
    assert result[0].field_name == "product_name"


# --- extract() top-level -----------------------------------------------------------

def test_extract_aggregates_across_multiple_pages():
    homepage = _page("https://example.com/", '<html><head><title>Example Store</title></head><body><a href="mailto:hi@example.com">Email</a></body></html>')
    contact_page = _page("https://example.com/contact", '<html><body><a href="tel:+15551234567">Call</a></body></html>')
    result = extract([homepage, contact_page])

    assert result.canonical_domain == "example.com"
    email_field = next(f for f in result.contacts if f.field_name == "email")
    phone_field = next(f for f in result.contacts if f.field_name == "phone")
    assert email_field.source_url == "https://example.com/"
    assert phone_field.source_url == "https://example.com/contact"


def test_extract_same_value_from_multiple_pages_produces_separate_field_entries():
    """Not deduped across pages — each page's discovery is a distinct,
    accurately-sourced observation (consistent with evidence being
    append-only)."""
    page1 = _page("https://example.com/", '<a href="mailto:hi@example.com">Email</a>')
    page2 = _page("https://example.com/contact", '<a href="mailto:hi@example.com">Email</a>')
    result = extract([page1, page2])
    email_fields = [f for f in result.contacts if f.field_name == "email"]
    assert len(email_fields) == 2
    assert {f.source_url for f in email_fields} == {"https://example.com/", "https://example.com/contact"}


def test_extract_page_with_no_useful_data_contributes_nothing():
    blank_page = _page("https://example.com/blog/some-post", "<html><body><p>Just a blog post, nothing extractable.</p></body></html>")
    result = extract([blank_page])
    assert result.contacts == []
    assert result.socials == []
    assert result.products == []


def test_extract_skips_unparseable_page_without_crashing():
    good_page = _page("https://example.com/", '<a href="mailto:hi@example.com">Email</a>')
    bad_page = _page("https://example.com/broken", None)  # raw_html=None gets filtered before parsing
    bad_page = FetchedPage(url="https://example.com/broken", page_type="other", fetched_via="http",
                            http_status_code=200, content_hash="x", raw_html="",
                            from_cache=False, fetched_at=FETCHED_AT)
    result = extract([good_page, bad_page])
    assert any(f.field_value == "hi@example.com" for f in result.contacts)


def test_extract_empty_pages_list_returns_empty_result():
    result = extract([])
    assert result.canonical_domain == ""
    assert result.business_fields == []
    assert result.contacts == []
    assert result.socials == []
    assert result.products == []


def test_extract_filters_out_cache_hit_pages_defensively():
    """extract() defensively filters pages with no raw_html even though
    jobs.py already does this filtering upstream — belt and suspenders."""
    cache_hit = FetchedPage(url="https://example.com/", page_type="homepage", fetched_via="http",
                             http_status_code=200, content_hash="x", raw_html=None,
                             from_cache=True, fetched_at=FETCHED_AT)
    result = extract([cache_hit])
    assert result.canonical_domain == ""  # nothing usable to derive it from


def test_extract_full_pipeline_realistic_shopify_style_page():
    html = f"""
    <html><head>
      <title>Example Store</title>
      <script type="application/ld+json">{{"@type": "Organization", "name": "Example Store", "sameAs": ["https://instagram.com/examplestore"]}}</script>
    </head>
    <body>
      <a href="mailto:hello@example.com">Contact</a>
      <a href="https://instagram.com/examplestore">Follow us</a>
    </body></html>
    """
    result = extract([_page("https://example-store.com/", html)])
    assert any(f.field_name == "name" and f.field_value == "Example Store" for f in result.business_fields)
    assert any(f.field_name == "email" and f.field_value == "hello@example.com" for f in result.contacts)
    assert any(f.field_name == "instagram" for f in result.socials)
