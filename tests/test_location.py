"""
Tests for etl/location.py.

Cases in REAL_* come from actual postings collected on 2026-11-01: the raw Jooble
file (data/raw/jooble/2026-11-01/) and the public boards of GitLab, Elastic,
Oscar, Tebra, Konrad, Critical Mass, Launch Potato (Greenhouse); Bluelight,
Penumbra, Spotify, Sysdig, BlueCat (Lever); Owner, Ramp, Zapier, Scale Army (Ashby).
API hints (workplace_type, is_remote, country_code) are the ones each posting had.

Run:  python -m pytest tests/
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "etl"))
from location import AMBIGUOUS, CR, LATAM_REMOTE, OUT_OF_SCOPE, US_REMOTE, classify_location  # noqa: E402

OUT = None  # expected market for excluded postings

# (location, hints, expected market, expected reason)
REAL_CR = [
    ("San Jose, Costa Rica", {}, CR, "cr_location"),                        # Critical Mass
    ("San Jose", {}, CR, "cr_location"),                                    # Critical Mass, no country
    ("San José", {}, CR, "cr_location"),                                    # Konrad, accent
    ("Costa Rica", {}, CR, "cr_location"),                                  # Konrad / Elastic / Tebra
    ("Heredia, Heredia, Costa Rica", {}, CR, "cr_location"),                # Tebra
    ("Liberia, Costa Rica", {"workplace_type": "remote", "country_code": "CR"}, CR, "cr_location"),  # Bluelight
    ("Cartago, Costa Rica", {"workplace_type": "onsite", "country_code": "CR"}, CR, "cr_location"),  # Penumbra
    ("Cartago, Costa Rica", {"workplace_type": "hybrid", "country_code": "CR"}, CR, "cr_location"),  # Penumbra
    ("Costa Rica - Remote", {"workplace_type": "remote", "country_code": "CR"}, CR, "cr_location"),  # BlueCat
    # CR wins even when the posting's main country is elsewhere
    ("Toronto Office | Costa Rica - Remote", {"workplace_type": "hybrid", "country_code": "CA"}, CR, "cr_location"),
    ("Flexible, Spain | Flexible - Costa Rica", {"workplace_type": "remote", "country_code": "ES"}, CR, "cr_location"),
    ("Alajuela, Costa Rica (remote); Cartago, Costa Rica (remote); Heredia, Costa Rica (remote)", {}, CR, "cr_location"),
    ("Remote - Colombia | Remote - Buenos Aires | Remote - Mexico | Remote - Costa Rica",
     {"workplace_type": "Remote", "is_remote": True}, CR, "cr_location"),                            # Owner
    (" Costa Rica | Guatemala | Chile | Ecuador | Mexico | Argentina | Honduras | Ethiopia",
     {"workplace_type": "Remote", "is_remote": True}, CR, "cr_location"),                            # Scale Army
]

REAL_US_REMOTE = [
    ("Remote, United States", {}, US_REMOTE, "us_remote"),                                           # GitLab
    ("Remote, US", {}, US_REMOTE, "us_remote"),                                                      # GitLab
    ("Remote, Canada; Remote, United States", {}, US_REMOTE, "us_remote"),                           # GitLab
    ("Remote, North America", {}, US_REMOTE, "us_remote"),                                           # GitLab
    ("United States - Remote", {}, US_REMOTE, "us_remote"),                                          # Tebra
    ("United States (remote)", {}, US_REMOTE, "us_remote"),                                          # Launch Potato
    ("Austin, TX (remote); Dallas, TX (remote); Oklahoma City, OK (remote)", {}, US_REMOTE, "us_remote"),
    ("Your Remote US Home Office", {"workplace_type": "remote", "country_code": "US"}, US_REMOTE, "us_remote"),  # Penumbra
    ("Flexible - USA", {"workplace_type": "remote", "country_code": "US"}, US_REMOTE, "us_remote"),  # Sysdig
    ("USA - Remote", {"workplace_type": "remote", "country_code": "US"}, US_REMOTE, "us_remote"),    # BlueCat
    ("Washington D.C. - Remote", {"workplace_type": "remote", "country_code": "US"}, US_REMOTE, "us_remote"),
    ("New York, NY", {"workplace_type": "remote", "country_code": "US"}, US_REMOTE, "us_remote"),    # Spotify
    ("Raleigh", {"workplace_type": "remote", "country_code": "US"}, US_REMOTE, "us_remote"),         # Sysdig: geo from country_code
    ("Remote - United States or Canada", {"workplace_type": None, "is_remote": None}, US_REMOTE, "us_remote"),  # Owner
    ("NAMER", {"workplace_type": "Remote", "is_remote": True}, US_REMOTE, "us_remote"),              # Zapier
    ("NAMER | India | European Union | EMEA", {"workplace_type": "Remote", "is_remote": True}, US_REMOTE, "us_remote"),
    # Region code after a specific place is a label, not "open to the Americas"
    ("Remote, California, United States, AMER", {}, US_REMOTE, "us_remote"),                         # Fivetran
    ("Remote-United-States", {}, US_REMOTE, "us_remote"),                                            # Acquia, hyphens
    # Hybrid posting that also offers a remote-US option
    ("New York, NY (HQ) | San Francisco, CA | Remote (US)", {"workplace_type": "Hybrid", "is_remote": True},
     US_REMOTE, "us_remote"),                                                                        # Ramp
]

REAL_LATAM_REMOTE = [
    ("South America", {"workplace_type": "remote", "country_code": "BR"}, LATAM_REMOTE, "latam_region"),  # Bluelight
    ("South America  | NAMER | Latin America", {"workplace_type": "Remote", "is_remote": True},
     LATAM_REMOTE, "latam_region"),                                                                  # Zapier: LATAM beats US
    ("Remote - Colombia | Remote - Buenos Aires | Remote - Mexico", {"workplace_type": "Remote", "is_remote": True},
     LATAM_REMOTE, "latam_multi_country"),                                                           # Owner
    ("Remote, Brazil; Remote, Mexico; Remote, United States", {}, LATAM_REMOTE, "latam_multi_country"),  # GitLab
    ("Egypt | Guatemala | Argentina | Honduras | Ethiopia | Jamaica | Colombia | Peru | Kenya",
     {"workplace_type": "Remote", "is_remote": True}, LATAM_REMOTE, "latam_multi_country"),          # Scale Army
]

REAL_OUT_OF_SCOPE = [
    ("Charleston, WV", {}, OUT, OUT_OF_SCOPE),                                                       # Jooble
    ("South Charleston, WV", {}, OUT, OUT_OF_SCOPE),                                                 # Jooble
    ("Belle, WV", {}, OUT, OUT_OF_SCOPE),                                                            # Jooble
    ("New York, New York, United States", {}, OUT, OUT_OF_SCOPE),                                    # Oscar, city-level, not remote
    ("Alameda, CA", {"workplace_type": "onsite", "country_code": "US"}, OUT, OUT_OF_SCOPE),          # Penumbra
    ("Alameda, CA", {"workplace_type": "hybrid", "country_code": "US"}, OUT, OUT_OF_SCOPE),          # hybrid US is not remote
    # Ashby isRemote=True but workplaceType=Hybrid: workplaceType wins
    ("New York, NY (HQ)", {"workplace_type": "Hybrid", "is_remote": True}, OUT, OUT_OF_SCOPE),       # Ramp
    ("Toronto", {"workplace_type": "Hybrid", "is_remote": True}, OUT, OUT_OF_SCOPE),                 # Owner
    ("Remote, United Kingdom", {}, OUT, OUT_OF_SCOPE),                                               # GitLab
    ("Remote Ireland; Remote, United Kingdom", {}, OUT, OUT_OF_SCOPE),                               # GitLab
    ("Remote, KSA; Remote, UAE", {}, OUT, OUT_OF_SCOPE),                                             # GitLab
    ("Bangalore, India", {}, OUT, OUT_OF_SCOPE),                                                     # GitLab
    ("Canada - Remote", {"workplace_type": "remote", "country_code": "CA"}, OUT, OUT_OF_SCOPE),      # BlueCat
    ("London | Stockholm", {"workplace_type": "remote", "country_code": "GB"}, OUT, OUT_OF_SCOPE),   # Spotify
    ("Major Metro cities", {"workplace_type": "remote", "country_code": "IN"}, OUT, OUT_OF_SCOPE),   # Penumbra: geo from country_code
    ("Your Remote New Zealand or Australia Home Office", {"workplace_type": "remote", "country_code": "AU"}, OUT, OUT_OF_SCOPE),
    ("EMEA", {"workplace_type": "Remote", "is_remote": True}, OUT, OUT_OF_SCOPE),                    # Zapier
    ("APAC", {"workplace_type": "Remote", "is_remote": True}, OUT, OUT_OF_SCOPE),                    # Zapier
    ("Warsaw | Europe", {"workplace_type": "hybrid", "country_code": "PL"}, OUT, OUT_OF_SCOPE),      # Penumbra
    # Remote restricted to ONE non-CR LATAM country: someone in Costa Rica can't apply
    ("Remote - Mexico", {"workplace_type": "Remote", "is_remote": True}, OUT, OUT_OF_SCOPE),         # Owner
    ("Remote, Brazil", {}, OUT, OUT_OF_SCOPE),                                                       # GitLab
    ("Managua, Nicaragua", {"workplace_type": "remote", "country_code": "NI"}, OUT, OUT_OF_SCOPE),   # Bluelight
    ("Lima, Peru (remote)", {}, OUT, OUT_OF_SCOPE),                                                  # Launch Potato
    ("Ciudad de Mexico, Mexico (remote); Guadalajara, Mexico (remote)", {}, OUT, OUT_OF_SCOPE),      # Launch Potato
    ("Egypt | Argentina | Ethiopia | South Africa | Nigeria", {"workplace_type": "Remote", "is_remote": True},
     OUT, OUT_OF_SCOPE),                                                                             # Scale Army: 1 LATAM country
    # Name collisions: these must NOT be read as US or Costa Rica
    ("Mexicali, Baja California, Mexico", {"workplace_type": "remote", "country_code": "MX"}, OUT, OUT_OF_SCOPE),  # "California"
    ("Bogota, D.C., Capital District, Colombia", {"workplace_type": "remote", "country_code": "CO"}, OUT, OUT_OF_SCOPE),  # "D.C."
    ("Santa Ana, El Salvador", {"workplace_type": "remote", "country_code": "SV"}, OUT, OUT_OF_SCOPE),  # not Santa Ana CR
    ("Cartagena, Bolivar, Colombia", {"workplace_type": "remote", "country_code": "CO"}, OUT, OUT_OF_SCOPE),  # not Cartago
    ("Toronto, ON", {"workplace_type": "OnSite", "is_remote": False}, OUT, OUT_OF_SCOPE),           # ON is not a US state
    ("Remote-Australia", {}, OUT, OUT_OF_SCOPE),                                                     # Acquia
    # Oowlish: Brazil-only list; unrecognised cities must not trigger the description fallback
    (["Brasília, Federal District, Brazil", "Greater Porto Alegre", "Greater Curitiba", "Salvador, Bahia",
      "Recife, Pernambuco, Brazil", "Belo Horizonte, Minas Gerais, Brazil"],
     {"workplace_type": "remote", "country_code": "BR"}, OUT, OUT_OF_SCOPE),
]


@pytest.mark.parametrize("location, hints, market, reason",
                         REAL_CR + REAL_US_REMOTE + REAL_LATAM_REMOTE + REAL_OUT_OF_SCOPE)
def test_real_locations(location, hints, market, reason):
    assert classify_location(location, **hints) == (market, reason)


# ---- location alone can't decide: title/description fallback ------------------------
# Description excerpts are real, from the postings named in the comments.
REAL_TEXT_FALLBACK = [
    # Oscar, location "Remote"
    ("Remote", "Associate Director, Actuarial",
     "This position is fully remote and open to candidates residing in the U.S., excluding Alaska; Delaware; Hawaii.",
     {}, US_REMOTE, "ambiguous_resolved_us_text"),
    # Oscar, location "Remote": only US state names
    ("Remote", "Nurse Practitioner",
     "2+ years of experience delivering virtual care Licensed in a minimum of 2 core Oscar States: "
     "Must have GA License Preferred: TX, FL or OH Licenses. Serving members in Texas, Georgia and Florida.",
     {}, US_REMOTE, "ambiguous_resolved_us_states"),
    # Owner, location "Remote"
    ("Remote", "Senior Software Engineer, Backend",
     "This role is 100% remote and can be based anywhere in the United States or Canada.",
     {"workplace_type": "Remote", "is_remote": True}, US_REMOTE, "ambiguous_resolved_us_text"),
    # Owner: on-site flag, but a bare "Remote" part and a remote-US description
    ("San Francisco | Remote", "Senior FP&A Analyst",
     "This role is 100% remote and can be based anywhere in the United States.",
     {"workplace_type": "OnSite", "is_remote": False}, US_REMOTE, "ambiguous_resolved_us_text"),
    # Scale Army, location "Remote"
    ("Remote", "Senior AI Software Engineer, Agent Systems",
     "This role is open to candidates based in LATAM, Africa, and Eastern Europe. Please note that as this "
     "role supports U.S.-based clients, candidates must be available to work during U.S. business hours.",
     {"workplace_type": "Remote", "is_remote": True}, LATAM_REMOTE, "ambiguous_resolved_latam_text"),
    # GitLab, location "Remote": the title places it in another country
    ("Remote", "Strategic Account Executive, France",
     "You will manage strategic accounts across the French market.", {}, OUT, OUT_OF_SCOPE),
    # "join us" must not count as "US"
    ("Remote", "General Future Opportunities", "Interested in joining us? Tell us about yourself.",
     {"workplace_type": "Remote", "is_remote": True}, OUT, AMBIGUOUS),
    # Elastic, location "United States" (no remote flag on Greenhouse)
    ("United States", "ABM Manager, Global Public Sector",
     "We take care of our people as a distributed company, diversity drives our identity.",
     {}, US_REMOTE, "us_region_remote_in_text"),
    ("Colorado, United States", "Account Executive", "Join our sales team in our Denver office.", {}, OUT, OUT_OF_SCOPE),
]


@pytest.mark.parametrize("location, title, description, hints, market, reason", REAL_TEXT_FALLBACK)
def test_text_fallback(location, title, description, hints, market, reason):
    assert classify_location(location, title, description, **hints) == (market, reason)


# ---- Spanish and format variants -----------------------------------------------------
# Not found in today's real data (sources are mostly English), but listed in the spec.
SYNTHETIC = [
    ("Remoto - Costa Rica", {}, CR, "cr_location"),
    ("Escazú, San José", {}, CR, "cr_location"),
    ("Remote - LATAM", {}, LATAM_REMOTE, "latam_region"),
    ("Remote - US or LATAM", {}, LATAM_REMOTE, "latam_region"),                   # LATAM word is an alternative, not a label
    ("Remote (Americas)", {}, LATAM_REMOTE, "latam_region"),
    ("Remoto (Latinoamérica)", {}, LATAM_REMOTE, "latam_region"),
    ("Teletrabajo - Centroamérica", {}, LATAM_REMOTE, "latam_region"),
    ("Anywhere", {}, LATAM_REMOTE, "worldwide"),
    ("Remote - Worldwide", {}, LATAM_REMOTE, "worldwide"),
    ("Anywhere in the US", {"workplace_type": "remote"}, US_REMOTE, "us_remote"),  # narrowed: not worldwide
    ("US Remote", {}, US_REMOTE, "us_remote"),
    ("Remote, Estados Unidos", {}, US_REMOTE, "us_remote"),
    ("Remote - New Mexico", {}, US_REMOTE, "us_remote"),                           # state, not the country
    ("San Jose, CA", {"workplace_type": "remote"}, US_REMOTE, "us_remote"),        # San Jose, California
    ("San Jose, California", {"workplace_type": "onsite"}, OUT, OUT_OF_SCOPE),
    ("Remoto - México", {}, OUT, OUT_OF_SCOPE),                                    # single non-CR country
    ("Remote", {}, OUT, AMBIGUOUS),
    ("", {}, OUT, AMBIGUOUS),
]


@pytest.mark.parametrize("location, hints, market, reason", SYNTHETIC)
def test_spanish_and_variants(location, hints, market, reason):
    assert classify_location(location, **hints) == (market, reason)


def test_accepts_list_of_locations():
    # Lever's categories.allLocations / Ashby's secondaryLocations as a list
    assert classify_location(["Toronto Office", "Costa Rica - Remote"], workplace_type="hybrid") == (CR, "cr_location")
    assert classify_location(["USA - Remote", "Toronto Office"], workplace_type="remote") == (US_REMOTE, "us_remote")


def test_none_and_html_description():
    assert classify_location(None, "Data Analyst", "<p>Open to candidates in <b>Latin America</b></p>") == \
        (LATAM_REMOTE, "ambiguous_resolved_latam_text")


# ---- whole raw postings (classify_posting) ----------------------------------------------
# tests/fixtures/real_postings.json holds 38 real postings from today's raw files
# (data/raw/ is git-ignored): location fields verbatim, descriptions trimmed to the
# sentences that matter. Built and checked so each trimmed posting classifies exactly
# like the full original.
import json  # noqa: E402

from location import classify_posting  # noqa: E402

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "real_postings.json").read_text(encoding="utf-8"))["postings"]


@pytest.mark.parametrize("case", FIXTURES, ids=[f"{c['source']}:{c['board']}:{c['why']}" for c in FIXTURES])
def test_real_postings(case):
    assert classify_posting(case["source"], case["job"]) == tuple(case["expected"])


def test_structured_country_beats_text():
    # Lever: "Melbourne" would read as Australia; country US and the FL abbreviation agree
    job = {"text": "Store Associate", "categories": {"location": "Melbourne, FL"}, "country": "US",
           "workplaceType": "remote", "descriptionPlain": ""}
    assert classify_posting("lever", job) == (US_REMOTE, "us_remote")
    # Ashby: the text says nothing useful, addressCountry decides
    job = {"title": "Analyst", "location": "GBR - LON Metro", "workplaceType": "Remote", "isRemote": True,
           "address": {"postalAddress": {"addressCountry": "United Kingdom"}},
           "descriptionPlain": "Our customers are in the U.S. and Europe."}
    assert classify_posting("ashby", job) == (OUT, OUT_OF_SCOPE)


def test_text_region_survives_structured_country():
    # One country field can't hold a region: "South America" + BR stays LATAM-remote
    job = {"text": "Engineer", "categories": {"location": "South America"}, "country": "BR",
           "workplaceType": "remote", "descriptionPlain": ""}
    assert classify_posting("lever", job) == (LATAM_REMOTE, "latam_region")


def test_lever_country_applies_to_primary_location_only():
    # Sysdig: country ES belongs to "Flexible, Spain"; the secondary Costa Rica location still counts
    job = {"text": "SRE", "categories": {"location": "Flexible, Spain",
                                         "allLocations": ["Flexible, Spain", "Flexible - Costa Rica"]},
           "country": "ES", "workplaceType": "remote", "descriptionPlain": ""}
    assert classify_posting("lever", job) == (CR, "cr_location")


def test_greenhouse_location_beats_office():
    # Reddit: remote US role attached to the San Francisco HQ office
    job = {"title": "Engineer", "location": {"name": "Remote - United States"},
           "offices": [{"name": "San Francisco", "location": "San Francisco, CA, United States"}], "content": ""}
    assert classify_posting("greenhouse", job) == (US_REMOTE, "us_remote")


def test_ambiguous_order_title_then_office_then_description():
    office_de = [{"name": "Remote Germany", "location": None}]
    us_boilerplate = "Applicants must be authorized to work in the United States."
    # title first
    job = {"title": "Account Executive (France)", "location": {"name": "Remote"}, "offices": office_de,
           "content": us_boilerplate}
    assert classify_posting("greenhouse", job) == (OUT, OUT_OF_SCOPE)
    # then office (beats the description's boilerplate)
    job = {"title": "Security Engineer", "location": {"name": "Remote"}, "offices": office_de, "content": us_boilerplate}
    assert classify_posting("greenhouse", job) == (OUT, OUT_OF_SCOPE)
    # then description, when the office has no geography
    job = {"title": "Security Engineer", "location": {"name": "Remote"}, "offices": [{"name": "Remote"}],
           "content": us_boilerplate}
    assert classify_posting("greenhouse", job) == (US_REMOTE, "ambiguous_resolved_us_text")
    # "Global" in a title is not a geography
    job = {"title": "Global Payroll Analyst", "location": {"name": "Remote"}, "offices": [], "content": ""}
    assert classify_posting("greenhouse", job) == (OUT, AMBIGUOUS)


def test_lever_html_description_fallback():
    # CSC "Seasonal Custom Boot Fitter": descriptionPlain empty, HTML description full
    job = {"text": "Analyst", "categories": {"location": "Remote"}, "workplaceType": "remote",
           "descriptionPlain": "", "description": "<p>Open to candidates across <b>Latin America</b>.</p>"}
    assert classify_posting("lever", job) == (LATAM_REMOTE, "ambiguous_resolved_latam_text")
