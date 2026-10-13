"""
Tests for restocking API endpoints.
"""
import pytest


class TestRestockRecommendations:
    """Test suite for GET /api/restocking/recommendations."""

    def test_recommendations_structure(self, client):
        """Test the response shape and per-item fields."""
        response = client.get("/api/restocking/recommendations?budget=500000")
        assert response.status_code == 200

        data = response.json()
        for field in ["budget", "total_cost", "remaining_budget", "lead_time_days", "items"]:
            assert field in data
        assert len(data["items"]) > 0

        for item in data["items"]:
            for field in [
                "sku", "name", "trend", "current_demand", "forecasted_demand",
                "quantity_on_hand", "unit_cost", "lead_time_days",
                "recommended_quantity", "line_cost", "partial",
            ]:
                assert field in item
            assert isinstance(item["recommended_quantity"], int)
            assert item["recommended_quantity"] > 0
            assert item["lead_time_days"] > 0

    def test_zero_budget_returns_no_items(self, client):
        """Test that a zero budget recommends nothing."""
        response = client.get("/api/restocking/recommendations?budget=0")
        assert response.status_code == 200

        data = response.json()
        assert data["items"] == []
        assert data["total_cost"] == 0
        assert data["lead_time_days"] == 0

    @pytest.mark.parametrize("budget", [1000, 50000, 150000, 500000])
    def test_spend_never_exceeds_budget(self, client, budget):
        """Test that planned spend stays within the budget and adds up."""
        data = client.get(f"/api/restocking/recommendations?budget={budget}").json()

        line_total = sum(item["line_cost"] for item in data["items"])
        assert data["total_cost"] <= budget
        assert abs(line_total - data["total_cost"]) < 0.01
        assert abs(data["total_cost"] + data["remaining_budget"] - budget) < 0.01

    def test_line_cost_calculation(self, client):
        """Test that each line cost is quantity times unit cost."""
        data = client.get("/api/restocking/recommendations?budget=500000").json()

        for item in data["items"]:
            assert abs(item["line_cost"] - item["recommended_quantity"] * item["unit_cost"]) < 0.01

    def test_full_budget_covers_whole_shortfall(self, client):
        """Test that with a large budget nothing is partial and quantity equals the shortfall."""
        data = client.get("/api/restocking/recommendations?budget=500000").json()

        for item in data["items"]:
            assert item["partial"] is False
            assert item["recommended_quantity"] == item["forecasted_demand"] - item["quantity_on_hand"]

    def test_only_items_with_shortfall_are_recommended(self, client):
        """Test that decreasing-demand or well-stocked items are never recommended."""
        data = client.get("/api/restocking/recommendations?budget=500000").json()
        skus = [item["sku"] for item in data["items"]]

        assert "MTR-304" not in skus  # decreasing demand, forecast below stock
        assert "PSU-501" not in skus  # stock already exceeds forecast

    def test_priority_is_by_demand_growth(self, client):
        """Test that items are ordered by largest forecast growth first."""
        data = client.get("/api/restocking/recommendations?budget=500000").json()

        growth = [i["forecasted_demand"] - i["current_demand"] for i in data["items"]]
        assert growth == sorted(growth, reverse=True)

    def test_small_budget_marks_partial(self, client):
        """Test that a budget too small for the top item buys a partial quantity."""
        data = client.get("/api/restocking/recommendations?budget=50000").json()

        assert len(data["items"]) > 0
        assert data["items"][0]["partial"] is True

    def test_order_lead_time_is_longest_item(self, client):
        """Test that the overall lead time is the longest among recommended items."""
        data = client.get("/api/restocking/recommendations?budget=500000").json()

        assert data["lead_time_days"] == max(i["lead_time_days"] for i in data["items"])

    def test_larger_budget_never_spends_less(self, client):
        """Test that raising the budget does not reduce planned spend."""
        low = client.get("/api/restocking/recommendations?budget=100000").json()
        high = client.get("/api/restocking/recommendations?budget=300000").json()

        assert high["total_cost"] >= low["total_cost"]

    def test_negative_budget_rejected(self, client):
        """Test that a negative budget fails validation."""
        response = client.get("/api/restocking/recommendations?budget=-1")
        assert response.status_code == 422

    def test_missing_budget_rejected(self, client):
        """Test that the budget parameter is required."""
        response = client.get("/api/restocking/recommendations")
        assert response.status_code == 422


class TestRestockOrders:
    """Test suite for POST/GET /api/restocking/orders."""

    def test_create_order(self, client):
        """Test submitting a restocking order."""
        payload = {"items": [{"sku": "WDG-001", "quantity": 10}, {"sku": "CTL-330", "quantity": 2}]}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RST-")
        assert order["status"] == "Submitted"
        assert len(order["items"]) == 2
        assert order["total_cost"] == 10 * 420.0 + 2 * 890.0

    def test_order_lead_time_and_delivery(self, client):
        """Test that lead time is the longest item and delivery date follows it."""
        from datetime import datetime

        payload = {"items": [{"sku": "GSK-203", "quantity": 5}, {"sku": "CTL-330", "quantity": 1}]}
        order = client.post("/api/restocking/orders", json=payload).json()

        # GSK-203 is 7 days, CTL-330 is 18 days
        assert order["lead_time_days"] == 18

        placed = datetime.fromisoformat(order["order_date"])
        delivery = datetime.fromisoformat(order["expected_delivery"])
        assert (delivery - placed).days == 18

    def test_order_uses_server_side_prices(self, client):
        """Test that unit costs come from inventory even if the client sends its own."""
        payload = {"items": [{"sku": "GSK-203", "quantity": 4, "unit_cost": 0.01}]}
        order = client.post("/api/restocking/orders", json=payload).json()

        assert order["items"][0]["unit_cost"] == 85.0
        assert order["total_cost"] == 340.0

    def test_submitted_order_appears_in_list_newest_first(self, client):
        """Test that submitted orders are listed, newest first."""
        first = client.post("/api/restocking/orders", json={"items": [{"sku": "SNR-420", "quantity": 1}]}).json()
        second = client.post("/api/restocking/orders", json={"items": [{"sku": "BRG-102", "quantity": 1}]}).json()

        response = client.get("/api/restocking/orders")
        assert response.status_code == 200

        ids = [o["id"] for o in response.json()]
        assert first["id"] in ids and second["id"] in ids
        assert ids.index(second["id"]) < ids.index(first["id"])

    def test_empty_order_rejected(self, client):
        """Test that an order with no items is rejected."""
        response = client.post("/api/restocking/orders", json={"items": []})
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_unknown_sku_rejected(self, client):
        """Test that an unknown SKU is rejected."""
        response = client.post("/api/restocking/orders", json={"items": [{"sku": "NOPE-999", "quantity": 1}]})
        assert response.status_code == 400
        assert "NOPE-999" in response.json()["detail"]

    @pytest.mark.parametrize("quantity", [0, -5])
    def test_non_positive_quantity_rejected(self, client, quantity):
        """Test that zero or negative quantities fail validation."""
        response = client.post("/api/restocking/orders", json={"items": [{"sku": "WDG-001", "quantity": quantity}]})
        assert response.status_code == 422

    def test_order_over_budget_rejected(self, client):
        """Test that an order costing more than the stated budget is rejected."""
        payload = {"items": [{"sku": "WDG-001", "quantity": 10}], "budget": 100}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()

    def test_rejected_order_is_not_stored(self, client):
        """Test that a failed submission does not add an order."""
        before = len(client.get("/api/restocking/orders").json())
        client.post("/api/restocking/orders", json={"items": [{"sku": "NOPE-999", "quantity": 1}]})
        after = len(client.get("/api/restocking/orders").json())

        assert after == before

    def test_recommendation_can_be_submitted_as_order(self, client):
        """Test the end-to-end flow: recommend within budget, then place that order."""
        budget = 150000
        rec = client.get(f"/api/restocking/recommendations?budget={budget}").json()
        payload = {
            "items": [{"sku": i["sku"], "quantity": i["recommended_quantity"]} for i in rec["items"]],
            "budget": budget,
        }
        response = client.post("/api/restocking/orders", json=payload)

        assert response.status_code == 201
        order = response.json()
        assert abs(order["total_cost"] - rec["total_cost"]) < 0.01
        assert order["lead_time_days"] == rec["lead_time_days"]
