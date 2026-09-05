from pathlib import Path

import pytest

from linkedin_agent.adapters.csv_import import parse_leads


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "leads.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_basic_and_custom_columns(tmp_path):
    p = write(
        tmp_path,
        "linkedin_url,first_name,last_name,company,title,location,custom_pain_point,Region\n"
        'https://www.linkedin.com/in/janedoe,Jane,Doe,Acme,VP Eng,"New York, NY",cloud costs,EMEA\n'
        "linkedin.com/in/bobsmith/,Bob,Smith,Contoso,CTO,Berlin,,\n",
    )
    r = parse_leads(p, "camp", "Europe/Sofia")
    assert len(r.leads) == 2 and r.skipped == []
    jane, bob = r.leads
    assert jane.timezone == "America/New_York" and bob.timezone == "Europe/Berlin"
    assert jane.custom_fields == {"custom_pain_point": "cloud costs", "region": "EMEA"}
    assert bob.linkedin_url == "https://linkedin.com/in/bobsmith/"
    assert r.custom_columns == {"custom_pain_point", "region"}
    assert jane.campaign == "camp"


def test_skips_invalid_and_duplicate_urls(tmp_path):
    p = write(
        tmp_path,
        "linkedin_url,name\n"
        "https://www.linkedin.com/company/acme,Acme\n"
        "https://www.linkedin.com/in/janedoe,Jane Doe\n"
        "https://www.linkedin.com/in/janedoe?trk=x,Jane Again\n"
        ",Nobody\n",
    )
    r = parse_leads(p, "camp")
    assert len(r.leads) == 1
    assert r.leads[0].first_name == "Jane" and r.leads[0].last_name == "Doe"
    assert [row for row, _ in r.skipped] == [2, 4, 5]
    assert "duplicate" in r.skipped[1][1]


def test_requires_url_column(tmp_path):
    p = write(tmp_path, "name,company\nJane,Acme\n")
    with pytest.raises(ValueError, match="linkedin_url"):
        parse_leads(p, "camp")


def test_bom_and_url_alias(tmp_path):
    p = write(tmp_path, "﻿url,timezone\nhttps://www.linkedin.com/in/x,Asia/Tokyo\n")
    r = parse_leads(p, "camp")
    assert r.leads[0].timezone == "Asia/Tokyo"
