"""
slug_utils.py  —  URL-safe slug helpers
"""
import re


def slugify(name: str) -> str:
    """'Archana Shukla' → 'archana-shukla'"""
    value = name.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-")


def unique_slug(name: str, existing_slugs: list) -> str:
    """
    Generate a slug guaranteed not to clash with existing ones.
    'Archana Shukla' when 'archana-shukla' exists → 'archana-shukla-2'
    """
    base = slugify(name)
    if base not in existing_slugs:
        return base
    counter = 2
    while f"{base}-{counter}" in existing_slugs:
        counter += 1
    return f"{base}-{counter}"


def is_valid_slug(slug: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9\-]*[a-z0-9]", slug))