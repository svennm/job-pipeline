"""ATS detection tests."""
import pytest

from src.ats import detect, SUPPORTED, BANNED


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/wintermute/jobs/4123456", "greenhouse"),
    ("https://job-boards.greenhouse.io/flowtraders/jobs/7708220", "greenhouse"),
    ("https://job-boards.eu.greenhouse.io/b2c2/jobs/4745320101", "greenhouse"),
    ("https://jobs.lever.co/flowdesk/abc-def", "lever"),
    ("https://jobs.lever.co/ion/b167acad", "lever"),
    ("https://jobs.ashbyhq.com/keyrock/123", "ashby"),
    ("https://apply.workable.com/some-co/j/ABC123", "workable"),
    ("https://citi.wd1.myworkdayjobs.com/CitiCareers/job/xxx", "workday"),
    ("https://www.linkedin.com/jobs/view/12345", "linkedin"),
    ("https://indeed.com/viewjob?jk=abc", "indeed"),
    ("https://some-broker.com/careers/swe", "unknown"),
])
def test_detect_known_hosts(url, expected):
    assert detect(url).name == expected


def test_detect_empty_url():
    h = detect("")
    assert h.name == "unknown"
    assert h.route == "manual"


def test_detect_none_url():
    h = detect(None)
    assert h.name == "unknown"


@pytest.mark.parametrize("name", sorted(SUPPORTED))
def test_supported_routes_to_prefill(name):
    # Pick a representative URL per supported ATS to confirm route mapping
    urls = {
        "greenhouse": "https://boards.greenhouse.io/co/jobs/1",
        "lever": "https://jobs.lever.co/co/abc",
        "ashby": "https://jobs.ashbyhq.com/co/1",
        "workable": "https://apply.workable.com/co/j/X",
        "workday": "https://co.wd1.myworkdayjobs.com/Careers/job/xxx",
    }
    h = detect(urls[name])
    assert h.supported
    assert not h.banned
    assert h.route == "prefill"


@pytest.mark.parametrize("name", sorted(BANNED))
def test_banned_routes_to_banned(name):
    urls = {
        "linkedin": "https://linkedin.com/jobs/view/1",
        "indeed": "https://indeed.com/x",
        "glassdoor": "https://glassdoor.com/x",
    }
    h = detect(urls[name])
    assert h.banned
    assert h.route == "banned"


def test_workday_routes_to_prefill():
    """V1 Workday handler — opens posting + clicks Apply, stops at sign-in."""
    h = detect("https://co.wd1.myworkdayjobs.com/CitiCareers/job/xxx")
    assert h.name == "workday"
    assert h.route == "prefill"
    assert h.supported
