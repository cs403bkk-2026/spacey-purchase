"""HTTP routes of the Purchase service. Each route parses the request, calls
one domain function and shapes the response - no business rules or SQL here."""

from flask import Blueprint

purchase_bp = Blueprint("purchase", __name__)
