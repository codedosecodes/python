import unittest

from esign.domain import (
    EnvelopeState,
    InvalidStateTransition,
    RouteRecipient,
    create_signing_token,
    hash_signing_token,
    next_routing_group,
    normalize_coordinate,
    normalized_to_pdf_rectangle,
    render_coordinate,
    require_transition,
    validate_field_value,
)
from esign.pdf import sha256, simple_pdf


class EnvelopeStateTests(unittest.TestCase):
    def test_valid_progression(self):
        require_transition(EnvelopeState.DRAFT, EnvelopeState.SENT)
        require_transition(EnvelopeState.SENT, EnvelopeState.COMPLETED)

    def test_terminal_state_cannot_change(self):
        with self.assertRaises(InvalidStateTransition):
            require_transition(EnvelopeState.COMPLETED, EnvelopeState.VOIDED)


class CoordinateTests(unittest.TestCase):
    def test_coordinates_round_trip_at_different_zoom(self):
        normalized = normalize_coordinate(306, 612)
        self.assertEqual(normalized, 0.5)
        self.assertEqual(render_coordinate(normalized, 918), 459)

    def test_outside_page_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_coordinate(613, 612)

    def test_pdf_coordinates_invert_browser_y_axis(self):
        rectangle = normalized_to_pdf_rectangle(
            x=0.5, y=0.75, width=0.25, height=0.1, page_width=612, page_height=792
        )
        self.assertAlmostEqual(rectangle.x, 306)
        self.assertAlmostEqual(rectangle.y, 118.8)
        self.assertAlmostEqual(rectangle.width, 153)
        self.assertAlmostEqual(rectangle.height, 79.2)

    def test_pdf_coordinates_support_rotation(self):
        rectangle = normalized_to_pdf_rectangle(
            x=0.1,
            y=0.2,
            width=0.3,
            height=0.1,
            page_width=600,
            page_height=800,
            rotation=90,
        )
        self.assertEqual(rectangle.width, 80)
        self.assertEqual(rectangle.height, 180)

    def test_pdf_rectangle_rejects_overflow(self):
        with self.assertRaises(ValueError):
            normalized_to_pdf_rectangle(
                x=0.9, y=0, width=0.2, height=0.1, page_width=600, page_height=800
            )


class FieldValidationTests(unittest.TestCase):
    def test_email_number_and_date_validation(self):
        self.assertEqual(
            validate_field_value("EMAIL", "jane@example.com", {}, required=True),
            "jane@example.com",
        )
        self.assertEqual(validate_field_value("NUMBER", "42.5", {}, required=True), "42.5")
        self.assertEqual(
            validate_field_value("DATE", "2026-09-23", {}, required=True), "2026-09-23"
        )

    def test_invalid_values_and_lengths_are_rejected(self):
        for field_type, value in (
            ("EMAIL", "not-email"),
            ("NUMBER", "forty"),
            ("DATE", "tomorrow"),
        ):
            with self.subTest(field_type=field_type), self.assertRaises(ValueError):
                validate_field_value(field_type, value, {}, required=True)
        with self.assertRaises(ValueError):
            validate_field_value("TEXT", "short", {"minLength": 10}, required=True)


class SigningTokenTests(unittest.TestCase):
    def test_token_hash_is_stored_instead_of_raw_secret(self):
        raw, digest = create_signing_token()
        self.assertNotEqual(raw, digest)
        self.assertEqual(hash_signing_token(raw), digest)
        self.assertEqual(len(digest), 64)

    def test_weak_entropy_is_rejected(self):
        with self.assertRaises(ValueError):
            create_signing_token(16)


class RoutingTests(unittest.TestCase):
    def test_only_next_pending_parallel_group_activates(self):
        recipients = [
            RouteRecipient("signed", 1, "SIGNED"),
            RouteRecipient("second-a", 2, "PENDING"),
            RouteRecipient("second-b", 2, "PENDING"),
            RouteRecipient("third", 3, "PENDING"),
        ]
        self.assertEqual(
            [item.identifier for item in next_routing_group(recipients)],
            ["second-a", "second-b"],
        )


class PdfTests(unittest.TestCase):
    def test_certificate_is_a_validly_framed_pdf_with_stable_hash(self):
        document = simple_pdf(["Certificate", "Envelope: example"])
        self.assertTrue(document.startswith(b"%PDF-1.4"))
        self.assertTrue(document.endswith(b"%%EOF\n"))
        self.assertEqual(len(sha256(document)), 64)


if __name__ == "__main__":
    unittest.main()
