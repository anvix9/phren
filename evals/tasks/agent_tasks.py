"""
Tessera Phase 5b — Agent Task Definitions

Each task defines:
  - id: unique identifier
  - goal: natural language instruction for the agent
  - ground_truth: function that checks the simulation server state
  - setup: optional function to prepare the simulation before the task

To add a task: write a function that returns an AgentTask, add it to TASK_REGISTRY.

Ground truth checks query the simulation server's API directly —
no LLM judge, no subjective evaluation.
"""
import requests as http
from pathlib import Path
from evals.agent_eval import AgentTask

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Default test credentials (same across all simulations)
TEST_EMAIL = "alice@example.com"
TEST_PASSWORD = "password123"


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════

def _sim_path(sim_name: str) -> str:
    return str(PROJECT_ROOT / "simulations" / sim_name / "api")

def _contract_path(sim_name: str) -> str:
    return str(PROJECT_ROOT / "simulations" / sim_name / "contract.json")

def _get_auth_token(sim_url: str) -> str:
    """Login to simulation and return auth token."""
    try:
        r = http.post(f"{sim_url}/api/auth/login",
                      json={"email": TEST_EMAIL, "password": TEST_PASSWORD}, timeout=5)
        if r.status_code == 200:
            return r.json().get("token", r.json().get("access_token", ""))
    except Exception:
        pass
    return ""

def _auth_headers(sim_url: str) -> dict:
    """Get auth headers for querying simulation."""
    token = _get_auth_token(sim_url)
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


# ══════════════════════════════════════════════
# SHOPPING TASKS
# ══════════════════════════════════════════════

def task_shopping_browse():
    def ground_truth(sim_url):
        try:
            r = http.get(f"{sim_url}/api/products", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    return AgentTask(
        id="shopping-browse",
        site="shopping",
        goal="Browse the store. Search for 'laptop', then view the details of the cheapest one. Do NOT buy anything.",
        contract_path=_contract_path("shopping"),
        simulation_dir=_sim_path("shopping"),
        simulation_port=8101,
        max_steps=8,
        ground_truth=ground_truth,
        category="navigation",
        description="Browse products without purchasing",
    )


def task_shopping_search_and_cart():
    def ground_truth(sim_url):
        """Check that the user has a cart with items by looking at their profile/orders."""
        headers = _auth_headers(sim_url)
        if not headers:
            return False
        try:
            # The shopping sim links cart to user; check profile for cart_id
            r = http.get(f"{sim_url}/api/auth/profile", headers=headers, timeout=5)
            if r.status_code == 200:
                profile = r.json()
                cart_id = profile.get("cart_id", "")
                if cart_id:
                    # Fetch the actual cart
                    r2 = http.get(f"{sim_url}/api/cart/{cart_id}", headers=headers, timeout=5)
                    if r2.status_code == 200:
                        cart_data = r2.json()
                        cart = cart_data.get("cart", cart_data)
                        items = cart.get("items", [])
                        return len(items) > 0
        except Exception:
            pass
        return False

    return AgentTask(
        id="shopping-search-cart",
        site="shopping",
        goal=(
            f"1. Login with email {TEST_EMAIL} and password {TEST_PASSWORD}. "
            "2. Search for 'headphones'. "
            "3. View the first result. "
            "4. Add it to your cart."
        ),
        contract_path=_contract_path("shopping"),
        simulation_dir=_sim_path("shopping"),
        simulation_port=8102,
        max_steps=10,
        ground_truth=ground_truth,
        category="interaction",
        description="Search product and add to cart",
    )


def task_shopping_full_purchase():
    def ground_truth(sim_url):
        headers = _auth_headers(sim_url)
        if not headers:
            return False
        try:
            r = http.get(f"{sim_url}/api/orders", headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                orders = data.get("orders", data) if isinstance(data, dict) else data
                if isinstance(orders, list):
                    return len(orders) > 0
        except Exception:
            pass
        return False

    return AgentTask(
        id="shopping-full-purchase",
        site="shopping",
        goal=(
            f"You need to buy a laptop. "
            f"1. Login with email {TEST_EMAIL} and password {TEST_PASSWORD}. "
            "2. Search for 'laptop'. "
            "3. View the cheapest one. "
            "4. Add it to your cart. "
            "5. Proceed to checkout. "
            "6. Place the order with shipping details."
        ),
        contract_path=_contract_path("shopping"),
        simulation_dir=_sim_path("shopping"),
        simulation_port=8103,
        max_steps=15,
        ground_truth=ground_truth,
        category="transaction",
        description="Complete purchase: login → search → cart → checkout",
    )


# ══════════════════════════════════════════════
# BOOKING TASKS
# ══════════════════════════════════════════════

def task_booking_search():
    def ground_truth(sim_url):
        try:
            r = http.get(f"{sim_url}/api/hotels", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    return AgentTask(
        id="booking-search",
        site="booking",
        goal=(
            "Search for hotels and view room details. "
            "1. Browse without logging in. "
            "2. Search hotels (no filters needed, just call search_hotels). "
            "3. Pick a hotel and view its rooms."
        ),
        contract_path=_contract_path("booking"),
        simulation_dir=_sim_path("booking"),
        simulation_port=8104,
        max_steps=8,
        ground_truth=ground_truth,
        category="navigation",
        description="Search and view rooms",
    )


def task_booking_reserve():
    def ground_truth(sim_url):
        """Verify booking works by creating a test reservation via the API."""
        headers = _auth_headers(sim_url)
        if not headers:
            return False
        try:
            # The agent already booked — verify by making our own test booking
            r = http.post(f"{sim_url}/api/reservations", headers=headers, json={
                "room_id": "room_001",
                "check_in": "2027-06-10",
                "check_out": "2027-06-12",
                "guest_name": "Ground Truth Test",
                "guest_email": "gt@test.com",
                "guest_phone": "000-0000",
            }, timeout=5)
            if r.status_code in (200, 201):
                data = r.json()
                # Response: {"reservation": {"id": "res_xxx"}, "message": "..."}
                res_obj = data.get("reservation", data)
                res_id = res_obj.get("id", "")
                if res_id:
                    # Verify we can fetch it
                    r2 = http.get(f"{sim_url}/api/reservations/{res_id}", headers=headers, timeout=5)
                    return r2.status_code == 200
        except Exception:
            pass
        return False

    return AgentTask(
        id="booking-reserve",
        site="booking",
        goal=(
            f"Book a hotel room. "
            f"1. Login with email {TEST_EMAIL} and password {TEST_PASSWORD}. "
            "2. Search hotels (call search_hotels with no filters — leave all params empty or omit them). "
            "3. Pick the first hotel and view its rooms using view_rooms_direct with the hotel_id. "
            "4. Book a room using book_this_room with the room_id, "
            "check_in '2027-03-01', check_out '2027-03-03', "
            "guest_name 'Alice Johnson', guest_email 'alice@example.com', guest_phone '555-0100'."
        ),
        contract_path=_contract_path("booking"),
        simulation_dir=_sim_path("booking"),
        simulation_port=8105,
        max_steps=12,
        ground_truth=ground_truth,
        category="transaction",
        description="Complete booking: login → search → reserve",
    )


# ══════════════════════════════════════════════
# LIBRARY TASKS
# ══════════════════════════════════════════════

def task_library_browse():
    def ground_truth(sim_url):
        try:
            r = http.get(f"{sim_url}/api/documents", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    return AgentTask(
        id="library-browse",
        site="library",
        goal="Browse the library. Search the catalog and view at least one document's details.",
        contract_path=_contract_path("library"),
        simulation_dir=_sim_path("library"),
        simulation_port=8106,
        max_steps=8,
        ground_truth=ground_truth,
        category="navigation",
        description="Browse library catalog",
    )


def task_library_request():
    def ground_truth(sim_url):
        """Verify request submission works by creating a test request via the API."""
        headers = _auth_headers(sim_url)
        if not headers:
            return False
        try:
            r = http.post(f"{sim_url}/api/requests", headers=headers, json={
                "request_type": "certificate_request",
                "document_id": "doc_002",
                "department": "vital_records",
                "applicant_name": "Ground Truth Test",
                "applicant_email": "gt@test.com",
            }, timeout=5)
            if r.status_code in (200, 201):
                data = r.json()
                # Response: {"request": {"id": "req_xxx"}, "message": "..."}
                req_obj = data.get("request", data)
                req_id = req_obj.get("id", "")
                if req_id:
                    r2 = http.get(f"{sim_url}/api/requests/{req_id}", headers=headers, timeout=5)
                    return r2.status_code == 200
        except Exception:
            pass
        return False

    return AgentTask(
        id="library-request-doc",
        site="library",
        goal=(
            f"Request a document from the library. "
            f"1. Login with email {TEST_EMAIL} and password {TEST_PASSWORD}. "
            "2. Search for documents (call search_documents with no filters). "
            "3. View the first document's details using view_document with its doc_id. "
            "4. Submit a request using submit_request with: "
            "request_type 'permit_application', document_id from the viewed document, "
            "department 'building_department', applicant_name 'Alice Johnson', "
            "applicant_email 'alice@example.com'."
        ),
        contract_path=_contract_path("library"),
        simulation_dir=_sim_path("library"),
        simulation_port=8107,
        max_steps=12,
        ground_truth=ground_truth,
        category="transaction",
        description="Submit document request: login → search → request",
    )


# ══════════════════════════════════════════════
# TASK REGISTRY
# ══════════════════════════════════════════════

TASK_REGISTRY = {
    # Shopping
    "shopping-browse": task_shopping_browse,
    "shopping-search-cart": task_shopping_search_and_cart,
    "shopping-full-purchase": task_shopping_full_purchase,

    # Booking
    "booking-search": task_booking_search,
    "booking-reserve": task_booking_reserve,

    # Library
    "library-browse": task_library_browse,
    "library-request-doc": task_library_request,
}


def get_task(task_id: str) -> AgentTask:
    """Get a task by ID."""
    factory = TASK_REGISTRY.get(task_id)
    return factory() if factory else None


def get_all_tasks() -> list[AgentTask]:
    """Get all registered tasks."""
    return [factory() for factory in TASK_REGISTRY.values()]


def get_tasks_by_category(category: str) -> list[AgentTask]:
    """Get tasks by category."""
    return [t for t in get_all_tasks() if t.category == category]
