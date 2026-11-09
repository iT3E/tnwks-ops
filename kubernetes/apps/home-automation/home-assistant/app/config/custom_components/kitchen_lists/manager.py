"""The lists shown on the wall display's Lists tab, and their settings.

Each list is a to-do entity (a Local To-do list, or the Shopping List
integration's todo.shopping_list) plus what only the display needs: its title
there, an emoji, whether its items carry an expiry date, and its place in the
row of lists. The tablet runs as a non-admin HA user, which can't start config
flows or edit the registries, so creating, renaming and deleting a list goes
through this integration's services instead.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import logging
from typing import Any

from homeassistant.components.todo import DATA_COMPONENT, TodoItem, TodoItemStatus
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_ICON,
    FEATURE_DUE_DATE,
    KEEP_DELETED,
    MAX_TITLE,
    PROTECTED,
    SEED,
    SIGNAL_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)
LOCAL_TODO = "local_todo"


def _clean_title(title: Any) -> str:
    title = " ".join(str(title or "").split())
    if not title:
        raise ServiceValidationError("A list needs a name.")
    if len(title) > MAX_TITLE:
        raise ServiceValidationError(f"Keep the name under {MAX_TITLE} characters.")
    return title


def _clean_icon(icon: Any) -> str:
    icon = str(icon or "").strip()
    return icon[:16] if icon else DEFAULT_ICON


def _item_dict(item: TodoItem) -> dict[str, Any]:
    due = item.due
    return {
        "summary": item.summary,
        "status": str(item.status),
        "description": item.description,
        "due": due.isoformat() if isinstance(due, (date, datetime)) else due,
    }


def _parse_due(value: str | None) -> date | datetime | None:
    if not value:
        return None
    if len(value) == 10:
        return date.fromisoformat(value)
    return dt_util.as_local(datetime.fromisoformat(value))


class KitchenLists:
    """The Lists tab's lists, kept in .storage/kitchen_lists."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.lists: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is None:
            self.lists = [dict(x) for x in SEED]
            await self._async_save()
        else:
            self.lists = list(data.get("lists", []))
            self.deleted = list(data.get("deleted", []))

    @callback
    def async_listen_registry(self) -> Callable[[], None]:
        """Follow entity ID renames and drop lists whose entity is removed."""

        @callback
        def changed(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
            data = event.data
            if data["action"] == "remove":
                before = len(self.lists)
                self.lists = [x for x in self.lists if x["entity_id"] != data["entity_id"]]
                if len(self.lists) != before:
                    self._changed()
            elif data["action"] == "update" and "old_entity_id" in data:
                for x in self.lists:
                    if x["entity_id"] == data["old_entity_id"]:
                        x["entity_id"] = data["entity_id"]
                        self._changed()

        return self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, changed)

    def as_attribute(self) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": x["entity_id"],
                "title": x["title"],
                "icon": x.get("icon") or DEFAULT_ICON,
                "expiry": bool(x.get("expiry")),
                "protected": x["entity_id"] in PROTECTED,
            }
            for x in self.lists
        ]

    async def _async_save(self) -> None:
        await self._store.async_save({"lists": self.lists, "deleted": self.deleted})

    @callback
    def _changed(self) -> None:
        self.hass.async_create_task(self._async_save())
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    def _find(self, entity_id: str) -> dict[str, Any] | None:
        return next((x for x in self.lists if x["entity_id"] == entity_id), None)

    def _title_taken(self, title: str, entity_id: str | None = None) -> bool:
        return any(
            x["title"].casefold() == title.casefold() and x["entity_id"] != entity_id
            for x in self.lists
        )

    def _features(self, entity_id: str) -> int:
        state = self.hass.states.get(entity_id)
        if state is None:
            raise ServiceValidationError(f"{entity_id} doesn't exist.")
        return int(state.attributes.get("supported_features") or 0)

    def _entity(self, entity_id: str):
        entity = self.hass.data[DATA_COMPONENT].get_entity(entity_id)
        if entity is None:
            raise ServiceValidationError(f"{entity_id} isn't loaded.")
        return entity

    async def async_create(self, title: Any, icon: Any = None, expiry: bool = False) -> str:
        """Create a Local To-do list and put it at the end of the row."""
        title = _clean_title(title)
        if not any(c.isalnum() for c in title):
            raise ServiceValidationError("Give the list a name with letters or numbers in it.")
        if self._title_taken(title):
            raise ServiceValidationError(f"There's already a list called {title}.")
        result = await self.hass.config_entries.flow.async_init(
            LOCAL_TODO, context={"source": SOURCE_USER}, data={"todo_list_name": title}
        )
        if result["type"] is not FlowResultType.CREATE_ENTRY:
            raise ServiceValidationError(
                f"Couldn't make {title}: {result.get('reason') or result['type']}."
            )
        entry = result["result"]
        ents = er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)
        if not ents:
            raise ServiceValidationError(f"{title} was made but has no list entity yet.")
        entity_id = ents[0].entity_id
        self.lists.append(
            {"entity_id": entity_id, "title": title, "icon": _clean_icon(icon), "expiry": bool(expiry)}
        )
        self._changed()
        _LOGGER.info("Created list %s (%s)", title, entity_id)
        return entity_id

    async def async_update(
        self,
        entity_id: str,
        title: Any = None,
        icon: Any = None,
        expiry: bool | None = None,
        position: int | None = None,
    ) -> None:
        """Change a list's settings; a to-do entity not on the tab yet is added."""
        features = self._features(entity_id)
        meta = self._find(entity_id)
        if meta is None:
            state = self.hass.states.get(entity_id)
            if not entity_id.startswith("todo."):
                raise ServiceValidationError(f"{entity_id} isn't a to-do list.")
            meta = {
                "entity_id": entity_id,
                "title": _clean_title(title or state.name),
                "icon": DEFAULT_ICON,
                "expiry": False,
            }
            if self._title_taken(meta["title"], entity_id):
                raise ServiceValidationError(f"There's already a list called {meta['title']}.")
            self.lists.append(meta)
        if title is not None:
            title = _clean_title(title)
            if self._title_taken(title, entity_id):
                raise ServiceValidationError(f"There's already a list called {title}.")
            meta["title"] = title
            reg = er.async_get(self.hass)
            ent = reg.async_get(entity_id)
            # A Local To-do list's own name follows, so HA's to-do panel matches.
            if ent and ent.platform == LOCAL_TODO and ent.config_entry_id:
                reg.async_update_entity(entity_id, name=title)
                if entry := self.hass.config_entries.async_get_entry(ent.config_entry_id):
                    self.hass.config_entries.async_update_entry(entry, title=title)
        if icon is not None:
            meta["icon"] = _clean_icon(icon)
        if expiry is not None:
            if expiry and not features & FEATURE_DUE_DATE:
                raise ServiceValidationError(f"{meta['title']} can't store dates, so it can't track expiry.")
            meta["expiry"] = bool(expiry)
        if position is not None:
            self.lists.remove(meta)
            self.lists.insert(max(0, min(int(position), len(self.lists))), meta)
        self._changed()

    async def async_delete(self, entity_id: str) -> None:
        """Delete a Local To-do list; if it had items, keep a copy for restore."""
        if entity_id in PROTECTED:
            raise ServiceValidationError("That list is part of the display and can't be deleted.")
        ent = er.async_get(self.hass).async_get(entity_id)
        if ent is None or ent.platform != LOCAL_TODO or not ent.config_entry_id:
            raise ServiceValidationError("Only lists made here (Local To-do lists) can be deleted.")
        meta = self._find(entity_id) or {"title": self.hass.states.get(entity_id).name, "icon": DEFAULT_ICON}
        try:
            items = [_item_dict(i) for i in self._entity(entity_id).todo_items or []]
        except ServiceValidationError:
            items = []
        # An empty list has nothing worth bringing back.
        if items:
            self.deleted.append(
                {
                    "entity_id": entity_id,
                    "title": meta["title"],
                    "icon": meta.get("icon") or DEFAULT_ICON,
                    "expiry": bool(meta.get("expiry")),
                    "deleted_at": dt_util.utcnow().isoformat(),
                    "items": items,
                }
            )
            self.deleted = self.deleted[-KEEP_DELETED:]
        await self.hass.config_entries.async_remove(ent.config_entry_id)
        # The registry listener drops it too; this covers a list not on the tab.
        self.lists = [x for x in self.lists if x["entity_id"] != entity_id]
        self._changed()
        _LOGGER.info("Deleted list %s (%s, %d items kept for restore)", meta["title"], entity_id, len(items))

    async def async_restore(self, title: Any = None) -> str:
        """Bring back the last deleted list (or the last one with that name)."""
        want = _clean_title(title).casefold() if title else None
        snap = next(
            (d for d in reversed(self.deleted) if want is None or d["title"].casefold() == want),
            None,
        )
        if snap is None:
            raise ServiceValidationError("There's no deleted list to bring back.")
        entity_id = await self.async_create(snap["title"], snap["icon"], snap["expiry"])
        entity = self._entity(entity_id)
        for i in snap["items"]:
            await entity.async_create_todo_item(
                TodoItem(
                    summary=i["summary"],
                    status=TodoItemStatus(i["status"]),
                    description=i.get("description"),
                    due=_parse_due(i.get("due")),
                )
            )
        self.deleted.remove(snap)
        self._changed()
        return entity_id
