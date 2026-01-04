from fastapi import Request
from fastapi.responses import HTMLResponse

from app import app, templates
import utils.globals as globals
from utils.configs import api_prefix


@app.get("/login", response_class=HTMLResponse)
async def login_html(request: Request):
    tokens_count = len(set(globals.token_list) - set(globals.error_token_list))
    response = templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "api_prefix": api_prefix,
            "tokens_count": tokens_count
        }
    )
    response.delete_cookie("token")
    return response
