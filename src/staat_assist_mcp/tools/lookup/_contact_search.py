"""Builds the Azure AI Search request for a contact search and reads its results.

Pure functions, no I/O. The field names are those of the contact index.
"""

import re
from collections.abc import Callable
from typing import Any

# Characters with a special meaning in Lucene query syntax.
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
_LUCENE_OPERATORS = {"AND", "OR", "NOT"}


def _digits(value: str) -> str:
    """Phone numbers are indexed as digits only. A trailing * (prefix search) is kept."""
    digits = re.sub(r"\D", "", value)
    return digits + "*" if digits and value.endswith("*") else digits


# Tool parameter -> (index field, how the value is normalized before searching).
SEARCH_FIELDS: dict[str, tuple[str, Callable[[str], str]]] = {
    "client_id": ("ip_cli_i", str.strip),
    "first_name": ("ip_frst_x_uc", str.upper),
    "last_name": ("ip_last_x_uc", str.upper),
    "phone": ("phones/tel_n", _digits),
    "area_code": ("phones/tel_area_c", _digits),
}

# Index field -> name in the tool result.
_CONTACT_FIELDS = {
    "ip_cli_i": "clientId",
    "ip_frst_x": "firstName",
    "ip_midl_x": "middleName",
    "ip_last_x": "lastName",
    "ip_pref_x": "prefix",
    "ip_sffx_x": "suffix",
    "ip_ttl_x_text": "title",
    "ip_gndr_c": "gender",
    "cli_stat_c": "status",
    "cli_trmtn_rsn_c": "terminationReason",
    "ip_dob_d": "dateOfBirth",
    "ip_dth_d": "dateOfDeath",
    "party_employer_name": "employer",
    "emailAddresses": "emails",
}
_ADDRESS_FIELDS = {
    "ads_i": "id",
    "ads_line_t": "line",
    "ads_city_long_x": "city",
    "ads_st_c": "state",
    "ads_zip_c": "zip",
    "ads_cntry_c": "countryCode",
    "ads_typ_c": "type",
    "ads_loc_c": "location",
}
_PHONE_FIELDS = {
    "tel_cntry_c": "countryCode",
    "tel_area_c": "areaCode",
    "tel_n": "number",
    "tel_extnn_n": "extension",
    "tel_labl_c": "label",
}
_ACCOUNT_FIELDS = {"prd_ow_i": "split", "acc_n": "accountNumber", "acc_stat_c": "status"}


def _escape_term(term: str) -> str:
    """Escape Lucene special characters, but keep a trailing * so prefix search still works."""
    if term in _LUCENE_OPERATORS:
        return f'"{term}"'
    is_prefix = len(term) > 1 and term.endswith("*")
    escaped = _LUCENE_SPECIAL.sub(r"\\\1", term[:-1] if is_prefix else term)
    return escaped + "*" if is_prefix else escaped


def escape_lucene(value: str) -> str:
    return " ".join(_escape_term(term) for term in value.split())


def build_search_request(
    text: str | None, attributes: dict[str, str], splits: list[str], top: int, skip: int
) -> dict[str, Any]:
    """The body of POST /docs/search. `attributes` maps SEARCH_FIELDS names to already normalized values."""
    if attributes:
        # Fielded search needs full Lucene syntax; free text becomes one more AND group.
        clauses = [f"{SEARCH_FIELDS[name][0]}:({escape_lucene(value)})" for name, value in attributes.items()]
        if text:
            clauses.append(f"({escape_lucene(text)})")
        search, query_type = " AND ".join(clauses), "full"
    else:
        # Plain free text: simple syntax is forgiving with whatever the user types.
        search, query_type = text, "simple"

    split_list = ",".join(split.replace("'", "''") for split in splits)
    return {
        "search": search,
        "queryType": query_type,
        "searchMode": "all",  # every term must match
        # Only contacts with an account in one of the user's splits.
        "filter": f"faInfoList/any(a: search.in(a/prd_ow_i, '{split_list}', ','))",
        "top": top,
        "skip": skip,
        "count": True,
    }


def _rename(source: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    return {new: source.get(old) for old, new in names.items()}


def to_contact(document: dict[str, Any], splits: list[str]) -> dict[str, Any]:
    """One search result with readable field names. Accounts outside the user's splits are left out."""
    allowed = {split.lower() for split in splits}
    contact = _rename(document, _CONTACT_FIELDS)
    contact["addresses"] = [_rename(a, _ADDRESS_FIELDS) for a in document.get("addresses") or []]
    contact["phones"] = [_rename(p, _PHONE_FIELDS) for p in document.get("phones") or []]
    contact["accounts"] = [
        _rename(account, _ACCOUNT_FIELDS)
        for account in document.get("faInfoList") or []
        if str(account.get("prd_ow_i") or "").lower() in allowed
    ]
    return contact
