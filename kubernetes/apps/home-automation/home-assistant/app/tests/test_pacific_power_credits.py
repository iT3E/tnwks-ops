"""Offline PDF-credit and migration tests; all statements are synthetic."""

import shutil
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock, patch
from uuid import uuid4

from custom_components.pacific_power_credits.bill import (
    MAX_PDF_BYTES,
    InvalidBill,
    parse_bill_pdf,
    parse_bill_text,
    validate_replacement,
)
from custom_components.pacific_power_credits.config_flow import CreditConfigFlow
from custom_components.pacific_power_credits.const import DOMAIN
from custom_components.pacific_power_credits.coordinator import CreditCoordinator
from custom_components.pacific_power_credits.sensor import CreditSensor
from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_registry import async_get as get_entity_registry
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
METERING = (
    "\n98765432M Oct 10, 2032 Nov 10, 2032 31 100 130 20.0 600 kwh\n"
    "98765432S Oct 10, 2032 Nov 10, 2032 200 240 20.0 -800 kwh\n"
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


def entry(version=2, retired_portal=False):
    snapshot = parse_bill_text(TEXT)
    data = {"mode": "bill", "snapshot": snapshot.as_dict()}
    if retired_portal:
        data.update(
            mode="portal",
            username="test@example.test",
            password=TEST_CREDENTIAL,
            customer_id="12345678",
            account_sequence="001",
        )
    return ConfigEntry(
        version=version,
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
            {
                "kwh",
                "statement_date",
                "expiry_month",
                "account_hash",
                "bill_import_kwh",
                "bill_export_kwh",
                "service_period_start",
                "service_period_end",
            },
        )

    def test_billed_grid_flows_check_multiplier_and_keep_no_meter_id(self):
        value = parse_bill_pdf(pdf_bytes(TEXT + METERING))
        self.assertEqual(value.bill_import_kwh, 600)
        self.assertEqual(value.bill_export_kwh, 800)
        self.assertEqual(value.service_period_start, "2032-10-10")
        self.assertEqual(value.service_period_end, "2032-11-10")
        self.assertNotIn("98765432", str(value.as_dict()))
        self.assertEqual(parse_bill_text(TEXT + METERING * 2), value)
        self.assertEqual(
            parse_bill_text(TEXT + METERING.replace("600 kwh", "600\nkwh")), value
        )

    def test_missing_or_invalid_metering_keeps_credit_but_no_invented_flows(self):
        for table in (
            "",
            METERING.replace("600 kwh", "601 kwh"),
            METERING.replace("-800", "800"),
            METERING.replace(" 240 ", " 150 "),
            METERING.replace("Nov 10, 2032 200", "Nov 09, 2032 200"),
            METERING.replace("Nov 10, 2032", "Dec 10, 2032"),
            METERING + METERING.replace("-800", "-900"),
            METERING + METERING.replace("98765432", "98765433"),
            METERING.replace("98765432S", "98765433S"),
        ):
            with self.subTest(table=table):
                value = parse_bill_text(TEXT + table)
                self.assertEqual(value.kwh, 2450.5)
                self.assertIsNone(value.bill_import_kwh)
                self.assertIsNone(value.bill_export_kwh)

    def test_explicit_zero_export_and_old_snapshot_compatibility(self):
        value = parse_bill_text(TEXT + METERING.replace("240 20.0 -800", "200 20.0 -0"))
        self.assertEqual(value.bill_export_kwh, 0)
        from custom_components.pacific_power_credits.bill import CreditBalance

        old = CreditBalance(100, "2032-11-12", "2033-03", "example-hash")
        self.assertIsNone(old.bill_import_kwh)

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


class HassTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.hass.config_entries = MagicMock()

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.temp.cleanup()

    async def test_imported_snapshot_has_no_polling(self):
        coordinator = CreditCoordinator(self.hass, entry())
        self.assertEqual(await coordinator._async_update_data(), parse_bill_text(TEXT))
        self.assertIsNone(coordinator.update_interval)

    async def test_sensor_units_date_and_no_grid_counter(self):
        coordinator = CreditCoordinator(self.hass, entry())
        sensor = CreditSensor(coordinator, "banked_energy")
        self.assertEqual(sensor.native_value, 2450.5)
        self.assertEqual(sensor.native_unit_of_measurement, "kWh")
        self.assertIsNone(sensor.state_class)
        self.assertEqual(sensor.extra_state_attributes["source"], "bill")
        self.assertEqual(
            CreditSensor(coordinator, "statement_date").native_value, date(2032, 11, 12)
        )
        self.assertEqual(
            CreditSensor(coordinator, "expiry_billing_month").native_value, "2033-03"
        )

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

    async def test_import_errors_leave_existing_snapshot_unchanged(self):
        flow = CreditConfigFlow()
        flow.hass = self.hass
        conf = entry()
        with (
            patch.object(
                flow, "_read_upload", side_effect=InvalidBill("missing_credit")
            ),
            patch.object(flow, "_existing_entry", return_value=conf),
        ):
            result = await flow.async_step_bill({"bill_pdf": "upload-id"})
        self.assertEqual(result["errors"], {"base": "invalid_bill"})
        self.assertEqual(conf.data["snapshot"]["kwh"], 2450.5)
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_reconfigure_accepts_only_same_account_newer_bill(self):
        flow = CreditConfigFlow()
        flow.hass = self.hass
        flow.context = {"source": "reconfigure"}
        conf = entry()
        newer = parse_bill_text(
            TEXT.replace("Nov 12", "Dec 12").replace("2,450.5", "1500")
        )
        with (
            patch.object(flow, "_existing_entry", return_value=conf),
            patch.object(
                flow, "async_update_reload_and_abort", return_value={"type": "abort"}
            ) as update,
        ):
            await flow._finish(newer)
            data = update.call_args.kwargs["data"]
            self.assertEqual(data, {"mode": "bill", "snapshot": newer.as_dict()})
            with self.assertRaisesRegex(InvalidBill, "wrong_account"):
                await flow._finish(
                    parse_bill_text(TEXT.replace("12345678", "87654321"))
                )


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        shutil.copytree(
            Path("/src/custom_components/pacific_power_credits"),
            self.directory / "custom_components" / "pacific_power_credits",
        )
        self.hass = HomeAssistant(self.temp.name)
        loader.async_setup(self.hass)
        self.hass.config.skip_pip = True
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
                self.hass,
            )
        )

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.temp.cleanup()

    def upload(self, text=TEXT):
        from homeassistant.components.file_upload import FileUploadData

        file_id = uuid4().hex
        directory = self.directory / "uploads"
        (directory / file_id).mkdir(parents=True)
        (directory / file_id / "bill.pdf").write_bytes(pdf_bytes(text))
        if "file_upload" not in self.hass.data:
            self.hass.data["file_upload"] = FileUploadData(directory, {})
        self.hass.data["file_upload"].files[file_id] = "bill.pdf"
        return file_id

    async def test_real_import_update_duplicate_migration_and_file_cleanup(self):
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "bill")
        self.assertEqual(
            [str(key) for key in result["data_schema"].schema], ["bill_pdf"]
        )
        file_id = self.upload()
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"bill_pdf": file_id}
        )
        self.assertEqual(result["type"], "create_entry")
        await self.hass.async_block_till_done()
        conf = result["result"]
        self.assertEqual(conf.state.value, "loaded")
        self.assertEqual(conf.version, 2)
        sensors = [
            state
            for state in self.hass.states.async_all("sensor")
            if state.attributes.get("unit_of_measurement") == "kWh"
        ]
        self.assertEqual(len(sensors), 1)
        entity_id = sensors[0].entity_id
        self.assertEqual(sensors[0].state, "2450.5")
        self.assertNotIn("state_class", sensors[0].attributes)
        self.assertFalse((self.directory / "uploads" / file_id).exists())

        result = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": conf.entry_id}
        )
        self.assertEqual(result["step_id"], "bill")
        file_id = self.upload(
            TEXT.replace("Nov 12", "Dec 12").replace("2,450.5", "1500") + METERING
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"bill_pdf": file_id}
        )
        self.assertEqual(result["reason"], "reconfigure_successful")
        await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get(entity_id).state, "1500.0")
        self.assertEqual(
            self.hass.states.get(entity_id).attributes["bill_import_kwh"], 600
        )
        self.assertEqual(
            self.hass.states.get(entity_id).attributes["bill_export_kwh"], 800
        )
        self.assertFalse((self.directory / "uploads" / file_id).exists())

        result = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        file_id = self.upload()
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"bill_pdf": file_id}
        )
        self.assertEqual(result["reason"], "already_configured")
        self.assertFalse((self.directory / "uploads" / file_id).exists())

        # Simulate upgrading the same registered entities from the retired mode.
        old_unique_id = conf.unique_id
        old_snapshot = dict(conf.data["snapshot"])
        legacy = entry(version=1, retired_portal=True)
        self.hass.config_entries.async_update_entry(
            conf, version=1, data={**legacy.data, "snapshot": old_snapshot}
        )
        await self.hass.config_entries.async_reload(conf.entry_id)
        await self.hass.async_block_till_done()
        self.assertEqual(conf.state.value, "loaded")
        self.assertEqual(conf.version, 2)
        self.assertEqual(conf.unique_id, old_unique_id)
        self.assertEqual(dict(conf.data), {"mode": "bill", "snapshot": old_snapshot})
        self.assertEqual(conf.runtime_data.data.kwh, 1500)
        self.assertEqual(self.hass.states.get(entity_id).state, "1500.0")
        self.assertIsNone(conf.runtime_data.update_interval)
        registry = get_entity_registry(self.hass)
        unique_ids = {
            entity.unique_id
            for entity in registry.entities.values()
            if entity.config_entry_id == conf.entry_id
        }
        self.assertEqual(
            unique_ids,
            {
                f"{old_unique_id}_{key}"
                for key in ("banked_energy", "statement_date", "expiry_billing_month")
            },
        )


if __name__ == "__main__":
    unittest.main()
