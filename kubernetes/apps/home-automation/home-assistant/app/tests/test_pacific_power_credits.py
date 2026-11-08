"""Offline tests against the pinned HA runtime; all bill data is synthetic."""

import base64
import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from custom_components.pacific_power_credits.bill import (
    MAX_PDF_BYTES,
    InvalidBill,
    parse_bill_pdf,
    parse_bill_text,
    validate_replacement,
)
from custom_components.pacific_power_credits.client import CreditClient
from custom_components.pacific_power_credits.config_flow import CreditConfigFlow
from custom_components.pacific_power_credits.const import DOMAIN
from custom_components.pacific_power_credits.coordinator import CreditCoordinator
from custom_components.pacific_power_credits.portal import (
    AccountInfo,
    PacificPowerApiError,
    PacificPowerAuthError,
)
from custom_components.pacific_power_credits.sensor import CreditSensor
from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

TEST_CREDENTIAL = "example-only"

NOTE = (
    "Note: You have excess power of 2,450.5 KWH available for future use "
    "until your billing in 03/2033."
)
TEXT = (
    "PACIFIC POWER\nBILLING DATE: Nov 12, 2032\nACCOUNT NUMBER: 12345678-001 0\n" + NOTE
)


def pdf_bytes(text=TEXT):
    """Build a text PDF in memory, without any real bill or external fixture."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 10 Tf 20 750 Td ("
            + escaped.replace("\n", ") Tj 0 -14 Td (")
            + ") Tj ET"
        ).encode()
    )
    page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def entry(snapshot=None, mode="bill"):
    snapshot = snapshot or parse_bill_text(TEXT)
    data = {"mode": mode, "snapshot": snapshot.as_dict()}
    if mode == "portal":
        data.update(
            username="test@example.test",
            password=TEST_CREDENTIAL,
            customer_id="12345678",
            account_sequence="001",
        )
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Solar credits",
        source="user",
        unique_id=snapshot.account_hash,
        data=data,
        options={},
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )


class BillTests(unittest.TestCase):
    def test_balance_context_and_no_identifying_text(self):
        value = parse_bill_text(TEXT)
        self.assertEqual(value.kwh, 2450.5)
        self.assertEqual(value.statement_date, "2032-11-12")
        self.assertEqual(value.expiry_month, "2033-03")
        self.assertNotIn("12345678", str(value.as_dict()))
        self.assertEqual(
            set(value.as_dict()),
            {"kwh", "statement_date", "expiry_month", "account_hash"},
        )

    def test_pdf_text_extraction(self):
        self.assertEqual(parse_bill_pdf(pdf_bytes()), parse_bill_text(TEXT))

    def test_repeated_note_and_wrapped_text(self):
        text = (TEXT + "\n" + NOTE).replace(
            "available for future use", "available\nfor\nfuture use"
        )
        self.assertEqual(parse_bill_text(text), parse_bill_text(TEXT))

    def test_lowercase_unformatted_and_explicit_zero(self):
        self.assertEqual(parse_bill_text(TEXT.lower().replace("2,450.5", "0")).kwh, 0)

    def test_missing_note_is_unknown_not_zero(self):
        with self.assertRaises(InvalidBill):
            parse_bill_text(TEXT.replace(NOTE, "No credit details on this statement"))

    def test_ambiguous_and_invalid_statements_rejected(self):
        cases = [
            TEXT + "\n" + NOTE.replace("2,450.5", "100"),
            TEXT + "\nACCOUNT NUMBER: 87654321-002",
            TEXT + "\nBILLING DATE: Dec 12, 2032",
            TEXT.replace("03/2033", "13/2033"),
            TEXT.replace("03/2033", "03/2031"),
            TEXT.replace("2,450.5", "-50"),
            TEXT.replace("2,450.5", "NaN"),
            TEXT.replace("2,450.5", "1,23"),
            TEXT.replace("KWH", "dollars"),
            TEXT.replace("PACIFIC POWER", "Other utility"),
            TEXT.replace("ACCOUNT NUMBER:", "Reference:"),
        ]
        for value in cases:
            with self.subTest(value=value[-80:]), self.assertRaises(InvalidBill):
                parse_bill_text(value)

    def test_invalid_or_oversized_pdf(self):
        for value in (b"not a PDF", b"%PDF-broken", b"%PDF-" + b"0" * MAX_PDF_BYTES):
            with self.assertRaisesRegex(InvalidBill, "invalid_pdf"):
                parse_bill_pdf(value)

    def test_replacement_guards_and_decreasing_balance(self):
        before = parse_bill_text(TEXT)
        after = parse_bill_text(
            TEXT.replace("Nov 12", "Dec 12").replace("2,450.5", "1500")
        )
        validate_replacement(before, after)
        with self.assertRaisesRegex(InvalidBill, "older_bill"):
            validate_replacement(after, before)
        with self.assertRaisesRegex(InvalidBill, "wrong_account"):
            validate_replacement(
                before, parse_bill_text(TEXT.replace("12345678", "87654321"))
            )


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = CreditClient("test@example.test", TEST_CREDENTIAL)
        self.client._web_user_id = "test-user"
        self.account = AccountInfo("12345678", "001", "", "")

    async def test_latest_statement_download_payload(self):
        self.client._api_call = AsyncMock(
            side_effect=[
                {
                    "getAccountDetailsResponseBody": {
                        "account": {"currentStatementDate": "2032-11-12"}
                    }
                },
                {
                    "getStatementPDFResponseBody": {
                        "PDFContent": base64.b64encode(pdf_bytes()).decode()
                    }
                },
            ]
        )
        result = await self.client.async_latest_balance(self.account)
        self.assertEqual(result, parse_bill_text(TEXT))
        self.assertEqual(
            self.client._api_call.call_args.args,
            (
                "/api/account-additional/getStatementPDF",
                {
                    "getStatementPDFRequestBody": {
                        "webUserID": "test-user",
                        "custAccountID": "12345678",
                        "custAccountSeq": "001",
                        "statementDate": "2032-11-12",
                    }
                },
            ),
        )

    async def test_unchanged_statement_not_downloaded(self):
        self.client._api_call = AsyncMock(
            return_value={
                "getAccountDetailsResponseBody": {
                    "account": {"currentStatementDate": "2032-11-12"}
                }
            }
        )
        previous = parse_bill_text(TEXT)
        self.assertIs(
            await self.client.async_latest_balance(self.account, previous), previous
        )
        self.assertEqual(self.client._api_call.call_count, 1)

    async def test_missing_statement_date(self):
        self.client._api_call = AsyncMock(return_value={})
        with self.assertRaises(PacificPowerApiError):
            await self.client.async_latest_balance(self.account)

    async def test_encrypted_api_round_trip(self):
        self.client._sign_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self.client._aes_key = os.urandom(32)
        iv = os.urandom(12)
        encrypted = AESGCM(self.client._aes_key).encrypt(iv, b'{"ok":true}', None)
        response = MagicMock()
        response.status = 200
        response.headers.getall.return_value = []
        response.text = AsyncMock(
            return_value=base64.b64encode(iv).decode()
            + base64.b64encode(encrypted).decode()
        )
        self.client._session = MagicMock()
        self.client._session.post.return_value.__aenter__ = AsyncMock(
            return_value=response
        )
        self.client._session.post.return_value.__aexit__ = AsyncMock(return_value=False)
        self.assertEqual(
            await self.client._api_call("/api/user/me", {"request": "example"}),
            {"ok": True},
        )
        request = self.client._session.post.call_args.kwargs
        content = request["data"].decode()
        plaintext = AESGCM(self.client._aes_key).decrypt(
            base64.b64decode(content[:16]), base64.b64decode(content[16:]), None
        )
        self.assertEqual(json.loads(plaintext), {"request": "example"})
        self.client._sign_key.public_key().verify(
            base64.b64decode(request["headers"]["X-WCSSS-Content-Signature"]),
            plaintext,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        response.status = 400
        response.text = AsyncMock(return_value="private-response-marker")
        with self.assertRaises(PacificPowerApiError) as caught:
            await self.client._api_call("/api/user/me", {})
        self.assertNotIn("private-response-marker", str(caught.exception))

    async def test_auth_redirect_restricted_to_utility_hosts(self):
        for url in (
            "http://csapps.pacificpower.net/login",
            "https://other.example.test/login",
        ):
            with self.assertRaises(PacificPowerAuthError):
                self.client._validate_auth_url(url)
        self.client._validate_auth_url("https://login.csapps.pacificpower.net/signin")

    async def test_bad_pdf_and_mismatched_bill_date(self):
        for encoded in (
            "not-base64!",
            base64.b64encode(b"not-pdf").decode(),
            base64.b64encode(pdf_bytes(TEXT.replace("Nov 12", "Oct 12"))).decode(),
        ):
            self.client._api_call = AsyncMock(
                side_effect=[
                    {
                        "getAccountDetailsResponseBody": {
                            "account": {"currentStatementDate": "2032-11-12"}
                        }
                    },
                    {"getStatementPDFResponseBody": {"PDFContent": encoded}},
                ]
            )
            with self.assertRaises(InvalidBill):
                await self.client.async_latest_balance(self.account)


class HassTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.hass.config_entries = MagicMock()

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.temp.cleanup()

    async def test_imported_snapshot_survives_restart_without_network(self):
        conf = entry()
        with patch(
            "custom_components.pacific_power_credits.coordinator.CreditClient"
        ) as client:
            coordinator = CreditCoordinator(self.hass, conf)
            self.assertEqual(
                await coordinator._async_update_data(), parse_bill_text(TEXT)
            )
            self.assertIsNone(coordinator.update_interval)
            client.assert_not_called()

    async def test_sensor_units_date_and_no_grid_counter(self):
        coordinator = CreditCoordinator(self.hass, entry())
        sensor = CreditSensor(coordinator, "banked_energy")
        self.assertEqual(sensor.native_value, 2450.5)
        self.assertEqual(sensor.native_unit_of_measurement, "kWh")
        self.assertIsNone(sensor.state_class)
        self.assertEqual(
            sensor.extra_state_attributes["expiry_billing_month"], "2033-03"
        )
        self.assertEqual(
            CreditSensor(coordinator, "statement_date").native_value, date(2032, 11, 12)
        )
        self.assertEqual(
            CreditSensor(coordinator, "expiry_billing_month").native_value, "2033-03"
        )

    async def test_new_cloud_bill_persisted_and_session_closed(self):
        conf = entry(mode="portal")
        after = parse_bill_text(
            TEXT.replace("Nov 12", "Dec 12").replace("2,450.5", "1500")
        )
        with (
            patch(
                "custom_components.pacific_power_credits.coordinator.CreditClient"
            ) as cls,
            patch.object(self.hass.config_entries, "async_update_entry") as update,
        ):
            api = cls.return_value
            for name in (
                "async_start",
                "async_login",
                "async_get_user_info",
                "async_stop",
            ):
                setattr(api, name, AsyncMock())
            api.async_latest_balance = AsyncMock(return_value=after)
            coordinator = CreditCoordinator(self.hass, conf)
            self.assertEqual(await coordinator._async_update_data(), after)
            self.assertEqual(
                update.call_args.kwargs["data"]["snapshot"], after.as_dict()
            )
            api.async_stop.assert_awaited_once()

    async def test_cloud_failure_preserves_snapshot_and_closes_session(self):
        for failure, expected in (
            (InvalidBill("missing_credit"), UpdateFailed),
            (PacificPowerAuthError("auth"), ConfigEntryAuthFailed),
        ):
            with patch(
                "custom_components.pacific_power_credits.coordinator.CreditClient"
            ) as cls:
                api = cls.return_value
                for name in (
                    "async_start",
                    "async_login",
                    "async_get_user_info",
                    "async_stop",
                ):
                    setattr(api, name, AsyncMock())
                api.async_latest_balance = AsyncMock(side_effect=failure)
                coordinator = CreditCoordinator(self.hass, entry(mode="portal"))
                with self.assertRaises(expected):
                    await coordinator._async_update_data()
                self.assertEqual(coordinator.data.kwh, 2450.5)
                api.async_stop.assert_awaited_once()

    async def test_upload_deleted_even_for_invalid_bill(self):
        from homeassistant.components.file_upload import FileUploadData

        directory = Path(self.temp.name) / "uploads"
        (directory / "upload-id").mkdir(parents=True)
        (directory / "upload-id" / "bill.pdf").write_bytes(b"not-pdf")
        self.hass.data["file_upload"] = FileUploadData(
            directory, {"upload-id": "bill.pdf"}
        )
        flow = CreditConfigFlow()
        flow.hass = self.hass
        with self.assertRaises(InvalidBill):
            await self.hass.async_add_executor_job(flow._read_upload, "upload-id")
        self.assertFalse((directory / "upload-id").exists())
        self.assertFalse(self.hass.data["file_upload"].files)

    async def test_pdf_setup_and_validation_errors(self):
        flow = CreditConfigFlow()
        flow.hass = self.hass
        with (
            patch.object(flow, "_read_upload", return_value=parse_bill_text(TEXT)),
            patch.object(
                flow, "_finish", new=AsyncMock(return_value={"type": "create_entry"})
            ),
        ):
            self.assertEqual(
                (await flow.async_step_bill({"bill_pdf": "upload-id"}))["type"],
                "create_entry",
            )
            self.assertEqual(flow._finish.call_args.args[1], {"mode": "bill"})
        with patch.object(
            flow, "_read_upload", side_effect=InvalidBill("missing_credit")
        ):
            result = await flow.async_step_bill({"bill_pdf": "upload-id"})
            self.assertEqual(result["errors"], {"base": "invalid_bill"})

    async def test_reconfigure_import_removes_credentials(self):
        flow = CreditConfigFlow()
        flow.hass = self.hass
        flow.context = {"source": "reconfigure"}
        conf = entry(mode="portal")
        with (
            patch.object(flow, "_existing_entry", return_value=conf),
            patch.object(
                flow, "async_update_reload_and_abort", return_value={"type": "abort"}
            ) as update,
        ):
            await flow._finish(parse_bill_text(TEXT), {"mode": "bill"})
            self.assertNotIn("password", update.call_args.kwargs["data"])
            self.assertEqual(update.call_args.kwargs["data"]["mode"], "bill")

    async def test_auth_failure_offers_import_instead_of_changing_mfa(self):
        flow = CreditConfigFlow()
        flow.hass = self.hass
        with patch(
            "custom_components.pacific_power_credits.config_flow.CreditClient"
        ) as cls:
            api = cls.return_value
            api.async_start = AsyncMock()
            api.async_login = AsyncMock(
                side_effect=PacificPowerAuthError("verification required")
            )
            api.async_stop = AsyncMock()
            result = await flow.async_step_portal(
                {"username": "test@example.test", "credential": TEST_CREDENTIAL}
            )
            self.assertEqual(result["errors"], {"base": "invalid_auth"})
            api.async_stop.assert_awaited_once()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_setup_entities_duplicate_and_reconfigure(self):
        """Run the real HA flow manager and sensor platform, without any cloud calls."""
        from homeassistant.components.file_upload import FileUploadData

        first_id, next_id, duplicate_id = (uuid4().hex for _ in range(3))
        with tempfile.TemporaryDirectory() as directory:
            source = Path("/src/custom_components/pacific_power_credits")
            destination = (
                Path(directory) / "custom_components" / "pacific_power_credits"
            )
            shutil.copytree(source, destination)
            hass = HomeAssistant(directory)
            loader.async_setup(hass)
            hass.config.skip_pip = True
            try:
                self.assertIsNotNone(
                    await bootstrap.async_from_config_dict(
                        {
                            "homeassistant": {
                                "latitude": 0,
                                "longitude": 0,
                                "time_zone": "UTC",
                                "country": "US",
                            },
                            "http": {"server_port": 8123},
                        },
                        hass,
                    )
                )
                self.assertIn("http", hass.config.components)
                result = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                self.assertEqual(result["type"], "menu")
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {"next_step_id": "bill"}
                )
                self.assertEqual(result["step_id"], "bill")
                upload_dir = Path(directory) / "upload"
                (upload_dir / first_id).mkdir(parents=True)
                (upload_dir / first_id / "bill.pdf").write_bytes(pdf_bytes())
                hass.data["file_upload"] = FileUploadData(
                    upload_dir, {first_id: "bill.pdf"}
                )
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {"bill_pdf": first_id}
                )
                self.assertEqual(result["type"], "create_entry")
                await hass.async_block_till_done()
                conf = result["result"]
                self.assertEqual(conf.state.value, "loaded")
                states = [
                    s
                    for s in hass.states.async_all("sensor")
                    if s.attributes.get("unit_of_measurement") == "kWh"
                ]
                self.assertEqual(len(states), 1)
                self.assertEqual(states[0].state, "2450.5")
                self.assertNotIn("state_class", states[0].attributes)
                self.assertNotIn("12345678", states[0].entity_id)

                # Updating through the real reconfigure flow reloads the integration.
                result = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "reconfigure", "entry_id": conf.entry_id}
                )
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {"next_step_id": "bill"}
                )
                (upload_dir / next_id).mkdir()
                (upload_dir / next_id / "bill.pdf").write_bytes(
                    pdf_bytes(
                        TEXT.replace("Nov 12", "Dec 12").replace("2,450.5", "1500")
                    )
                )
                hass.data["file_upload"].files[next_id] = "bill.pdf"
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {"bill_pdf": next_id}
                )
                self.assertEqual(result["type"], "abort")
                await hass.async_block_till_done()
                self.assertEqual(hass.states.get(states[0].entity_id).state, "1500.0")
                self.assertEqual(conf.data["snapshot"]["kwh"], 1500)
                self.assertFalse((upload_dir / next_id).exists())

                # Duplicate account imports fail before adding another entry.
                result = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {"next_step_id": "bill"}
                )
                (upload_dir / duplicate_id).mkdir()
                (upload_dir / duplicate_id / "bill.pdf").write_bytes(pdf_bytes())
                hass.data["file_upload"].files[duplicate_id] = "bill.pdf"
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {"bill_pdf": duplicate_id}
                )
                self.assertEqual(result["reason"], "already_configured")
            finally:
                await hass.async_stop()


if __name__ == "__main__":
    unittest.main()
