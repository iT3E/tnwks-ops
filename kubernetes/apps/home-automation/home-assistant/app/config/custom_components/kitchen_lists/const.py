"""Constants for the Kitchen lists integration."""

DOMAIN = "kitchen_lists"
STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1
SIGNAL_UPDATED = f"{DOMAIN}_updated"
SENSOR_ENTITY_ID = "sensor.kitchen_lists"

DEFAULT_ICON = "📝"
MAX_TITLE = 40
# Deleted lists are kept (with their items) so one can be brought back.
KEEP_DELETED = 10

# The Lists tab for a fresh install. Groceries is a Local To-do list (since
# 2026-11-08; the Shopping List integration's todo.shopping_list can't store
# notes or dates), so its items can carry an expiry date.
SEED = [
    {"entity_id": "todo.groceries", "title": "Groceries", "icon": "🛒", "expiry": True},
    {"entity_id": "todo.things_to_do", "title": "Things to do", "icon": "✅", "expiry": False},
    {"entity_id": "todo.someday", "title": "Someday", "icon": "💭", "expiry": False},
]

# Lists other parts of the display depend on: never deleted from the tablet.
PROTECTED = frozenset(
    {
        "todo.groceries",
        "todo.notes",
        "todo.kitchen_routines",
        "todo.kitchen_rewards",
        "todo.kitchen_stars_log",
        "todo.screensaver_albums",
    }
)

# TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
FEATURE_DUE_DATE = 16
