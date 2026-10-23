# services/networks.py
"""Guess a Nigerian number's network from its prefix. Only a hint: numbers can be ported between networks."""

PREFIXES = {
    "mtn": ["0703", "0704", "0706", "0803", "0806", "0810", "0813", "0814", "0816", "0903", "0906", "0913", "0916"],
    "glo": ["0705", "0805", "0807", "0811", "0815", "0905", "0915"],
    "airtel": ["0701", "0708", "0802", "0808", "0812", "0901", "0902", "0904", "0907", "0912"],
    "9mobile": ["0809", "0817", "0818", "0908", "0909"],
}
NETWORK_BY_PREFIX = {prefix: network for network, prefixes in PREFIXES.items() for prefix in prefixes}


def detect_network(phone: str) -> str | None:
    """mtn, glo, airtel or 9mobile for an 11-digit number like 0803..., or None if the prefix is unknown."""
    return NETWORK_BY_PREFIX.get((phone or "")[:4])
