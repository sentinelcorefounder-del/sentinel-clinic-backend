import importlib
import os
import sys
from unittest import TestCase
from unittest.mock import patch


class ProductionCorsSettingsTests(TestCase):
    def load_production_settings(self, **environment):
        environment.setdefault("DATABASE_URL", "sqlite:///:memory:")
        with patch.dict(os.environ, environment, clear=True):
            sys.modules.pop("config.settings.production", None)
            return importlib.import_module("config.settings.production")

    def test_unset_regexes_are_empty(self):
        production = self.load_production_settings()

        self.assertEqual(production.CORS_ALLOWED_ORIGIN_REGEXES, [])

    def test_empty_regexes_are_empty(self):
        production = self.load_production_settings(
            CORS_ALLOWED_ORIGIN_REGEXES="",
        )

        self.assertEqual(production.CORS_ALLOWED_ORIGIN_REGEXES, [])

    def test_comma_separated_regexes_are_parsed(self):
        production = self.load_production_settings(
            CORS_ALLOWED_ORIGIN_REGEXES=(
                r"^https://preview-one\.example$ , ^https://preview-two\.example$"
            ),
        )

        self.assertEqual(
            production.CORS_ALLOWED_ORIGIN_REGEXES,
            [
                r"^https://preview-one\.example$",
                r"^https://preview-two\.example$",
            ],
        )

    def test_blank_and_malformed_regexes_are_discarded(self):
        production = self.load_production_settings(
            CORS_ALLOWED_ORIGIN_REGEXES=(
                r"  , ^https://valid\.example$ , [invalid ,   "
            ),
        )

        self.assertEqual(
            production.CORS_ALLOWED_ORIGIN_REGEXES,
            [r"^https://valid\.example$"],
        )

    def test_staging_exact_origin_does_not_inherit_production_regexes(self):
        staging_origin = "https://sentinel-staging.example"
        production = self.load_production_settings(
            CORS_ALLOWED_ORIGINS=staging_origin,
            CSRF_TRUSTED_ORIGINS=staging_origin,
        )

        self.assertEqual(production.CORS_ALLOWED_ORIGINS, [staging_origin])
        self.assertEqual(production.CSRF_TRUSTED_ORIGINS, [staging_origin])
        self.assertEqual(production.CORS_ALLOWED_ORIGIN_REGEXES, [])

    def test_production_and_vercel_regexes_require_explicit_configuration(self):
        production_regex = r"^https://ops\.usesentinelhealth\.com$"
        vercel_regex = r"^https://sentinel-preview-.*\.vercel\.app$"
        production = self.load_production_settings(
            CORS_ALLOWED_ORIGIN_REGEXES=f"{production_regex},{vercel_regex}",
        )

        self.assertEqual(
            production.CORS_ALLOWED_ORIGIN_REGEXES,
            [production_regex, vercel_regex],
        )

    def test_exact_cors_and_csrf_values_still_discard_blank_entries(self):
        production = self.load_production_settings(
            CORS_ALLOWED_ORIGINS=(
                "https://frontend-one.example, , https://frontend-two.example"
            ),
            CSRF_TRUSTED_ORIGINS=(
                "https://frontend-one.example, , https://frontend-two.example"
            ),
        )

        expected = [
            "https://frontend-one.example",
            "https://frontend-two.example",
        ]
        self.assertEqual(production.CORS_ALLOWED_ORIGINS, expected)
        self.assertEqual(production.CSRF_TRUSTED_ORIGINS, expected)

    def test_debug_and_secure_cookie_settings_remain_production_safe(self):
        production = self.load_production_settings(
            SESSION_COOKIE_SECURE="False",
            CSRF_COOKIE_SECURE="False",
        )

        self.assertIs(production.DEBUG, False)
        self.assertIs(production.SESSION_COOKIE_SECURE, True)
        self.assertIs(production.CSRF_COOKIE_SECURE, True)
