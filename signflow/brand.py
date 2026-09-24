from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Brand:
    name: str = "SignFlow"
    short_name: str = "SF"
    logo_path: str = "branding/logo.svg"
    primary_color: str = "#1769e0"
    support_email: str = "support@signflow.example"


brand = Brand()


def brand_context(_request):
    return {"brand": brand}
