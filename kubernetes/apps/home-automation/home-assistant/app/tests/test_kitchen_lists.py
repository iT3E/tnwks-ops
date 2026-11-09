"""Kitchen lists: create, change, delete and restore lists in a real HA instance."""

import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

DOMAIN = "kitchen_lists"


def free_port():
    # Each HA instance gets its own port: a stopped one can still hold the last.
    # HA binds IPv4 and IPv6 separately, so check the port is free on both.
    with socket.socket(socket.AF_INET6) as s:
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        s.bind(("::", 0))
        return s.getsockname()[1]


class KitchenListsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        shutil.copytree(
            Path("/src/custom_components/kitchen_lists"),
            self.directory / "custom_components" / "kitchen_lists",
        )
        await self.start()

    async def start(self):
        # HA keeps the last HTTP settings in .storage and tries them first, and
        # the stopped instance can still hold that port; start from the YAML.
        for f in (self.directory / ".storage").glob("http*"):
            f.unlink()
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
                    "http": {"server_port": free_port()},
                    DOMAIN: {},
                },
                self.hass,
            )
        )
        await self.hass.async_block_till_done()

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.temp.cleanup()

    def lists(self):
        state = self.hass.states.get("sensor.kitchen_lists")
        return [(x["entity_id"], x["title"], x["icon"], x["expiry"], x["protected"]) for x in state.attributes["lists"]]

    async def call(self, service, data, response=False):
        return await self.hass.services.async_call(
            DOMAIN, service, data, blocking=True, return_response=response
        )

    async def todo(self, service, entity_id, data=None, response=False):
        return await self.hass.services.async_call(
            "todo", service, {"entity_id": entity_id, **(data or {})}, blocking=True, return_response=response
        )

    async def items(self, entity_id):
        r = await self.todo("get_items", entity_id, response=True)
        return r[entity_id]["items"]

    async def test_seed_matches_the_old_tab(self):
        self.assertEqual(
            self.lists(),
            [
                ("todo.shopping_list", "Groceries", "🛒", False, True),
                ("todo.things_to_do", "Things to do", "✅", False, False),
                ("todo.someday", "Someday", "💭", False, False),
            ],
        )
        self.assertEqual(self.hass.states.get("sensor.kitchen_lists").state, "3")

    async def test_create_update_delete_restore(self):
        r = await self.call("create", {"title": " Fridge  ", "icon": "🧊", "expiry": True}, True)
        self.assertEqual(r, {"entity_id": "todo.fridge"})
        await self.hass.async_block_till_done()
        st = self.hass.states.get("todo.fridge")
        self.assertIsNotNone(st)
        self.assertTrue(int(st.attributes["supported_features"]) & 16)
        self.assertEqual(self.lists()[-1], ("todo.fridge", "Fridge", "🧊", True, False))

        with self.assertRaisesRegex(ServiceValidationError, "already a list called"):
            await self.call("create", {"title": "fridge"})
        with self.assertRaisesRegex(ServiceValidationError, "letters or numbers"):
            await self.call("create", {"title": "🥶"})

        await self.todo("add_item", "todo.fridge", {"item": "Milk", "due_date": "2026-11-12"})
        await self.todo("add_item", "todo.fridge", {"item": "Yogurt", "description": "the big tub"})
        await self.todo("update_item", "todo.fridge", {"item": "Yogurt", "status": "completed"})
        self.assertTrue((self.directory / ".storage" / "local_todo.fridge.ics").exists())

        await self.call("update", {"entity_id": "todo.fridge", "title": "Fridge door", "icon": "🥛", "position": 0})
        await self.hass.async_block_till_done()
        self.assertEqual(self.lists()[0], ("todo.fridge", "Fridge door", "🥛", True, False))
        self.assertEqual(self.hass.states.get("todo.fridge").name, "Fridge door")
        entry = self.hass.config_entries.async_entries("local_todo")[0]
        self.assertEqual(entry.title, "Fridge door")
        with self.assertRaisesRegex(ServiceValidationError, "already a list called"):
            await self.call("update", {"entity_id": "todo.fridge", "title": "groceries"})
        await self.call("update", {"entity_id": "todo.fridge", "position": 99})
        self.assertEqual(self.lists()[-1][0], "todo.fridge")

        with self.assertRaisesRegex(ServiceValidationError, "part of the display"):
            await self.call("delete", {"entity_id": "todo.shopping_list"})
        with self.assertRaisesRegex(ServiceValidationError, "part of the display"):
            await self.call("delete", {"entity_id": "todo.kitchen_routines"})

        await self.call("delete", {"entity_id": "todo.fridge"})
        await self.hass.async_block_till_done()
        self.assertIsNone(self.hass.states.get("todo.fridge"))
        self.assertEqual(self.hass.config_entries.async_entries("local_todo"), [])
        self.assertIsNone(er.async_get(self.hass).async_get("todo.fridge"))
        self.assertFalse((self.directory / ".storage" / "local_todo.fridge.ics").exists())
        self.assertEqual([x[0] for x in self.lists()], ["todo.shopping_list", "todo.things_to_do", "todo.someday"])

        r = await self.call("restore", {}, True)
        await self.hass.async_block_till_done()
        eid = r["entity_id"]
        self.assertEqual(self.lists()[-1][1:4], ("Fridge door", "🥛", True))
        got = {(i["summary"], i["status"], i.get("due"), i.get("description")) for i in await self.items(eid)}
        self.assertEqual(
            got,
            {("Milk", "needs_action", "2026-11-12", None), ("Yogurt", "completed", None, "the big tub")},
        )
        with self.assertRaisesRegex(ServiceValidationError, "no deleted list"):
            await self.call("restore", {})

    async def test_expiry_needs_dates_and_settings_persist(self):
        # A to-do list that isn't on the tab yet is added by update.
        await self.call("create", {"title": "Pantry"})
        with self.assertRaisesRegex(ServiceValidationError, "doesn't exist"):
            await self.call("update", {"entity_id": "todo.nothing_here", "expiry": True})
        await self.call("update", {"entity_id": "todo.pantry", "expiry": True, "position": 1})
        await self.hass.async_block_till_done()
        before = self.lists()
        self.assertEqual(before[1], ("todo.pantry", "Pantry", "📝", True, False))
        await self.hass.async_stop()
        await self.start()
        self.assertEqual(self.lists(), before)

    async def test_list_removed_in_ha_leaves_the_tab(self):
        await self.call("create", {"title": "Hardware store"})
        await self.hass.async_block_till_done()
        entry = self.hass.config_entries.async_entries("local_todo")[0]
        await self.hass.config_entries.async_remove(entry.entry_id)
        await self.hass.async_block_till_done()
        self.assertNotIn("todo.hardware_store", [x[0] for x in self.lists()])


if __name__ == "__main__":
    unittest.main()
