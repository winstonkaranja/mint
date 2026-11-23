from mint.auth import azure_issuer


def test_single_tenant_pins_issuer():
    assert azure_issuer("ad248388-f384-41e0-9740-0dc41e679fee") == (
        "https://login.microsoftonline.com/ad248388-f384-41e0-9740-0dc41e679fee/v2.0"
    )


def test_multi_tenant_accepts_any_issuing_tenant():
    for t in ("common", "organizations", "consumers"):
        assert azure_issuer(t) is None
