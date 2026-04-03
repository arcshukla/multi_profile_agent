from fastapi import Request, Form
from fastapi.responses import HTMLResponse
from app.services.pushover_template_service import get_all_templates, save_template, restore_default
from app.core.logging_config import get_logger

logger = get_logger(__name__)

def get_pushover_templates_partial(request: Request):
    return {
        "pushover_templates": get_all_templates(),
    }

def update_pushover_template(name: str, body_text: str):
    templates = get_all_templates()
    if name not in templates:
        return False
    data = templates[name]
    data["body_text"] = body_text
    save_template(name, data)
    return True

def restore_pushover_template(name: str):
    restore_default(name)
    return True
