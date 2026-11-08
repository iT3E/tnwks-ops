"""Home Automation & Cameras (L2): Are automations and recording useful, including their hardware dependencies?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import metric


def board() -> Board:
    return Board("tnwks-home", "Home Automation & Cameras", "home", "l2",
              "Are automations and recording useful, including their hardware dependencies?", {
            "Cameras": [
                p("Recording input", f'sum by (k8s_cluster) ({metric("frigate_camera_fps")})', "fps",
                  "Aggregate camera input FPS without camera/zone/person identifiers. Input FPS alone does not prove recordings were persisted."),
                p("Skipped frames", f'sum by (k8s_cluster) ({metric("frigate_skipped_fps")})', "fps",
                  "Aggregate skipped frames; inspect host/detector load and source health before drawing conclusions."),
                p("Detector inference", f'max by (k8s_cluster, detector) ({metric("frigate_detector_inference_speed_seconds")})', "s",
                  "Detector-specific inference latency; hardware absent by design is not a failed detector."),
            ],
            "Automation": [
                p("Home Assistant path", metric("homelab:service_state", 'app="home-assistant"'), "state",
                  "Path probe with freshness. USB/Z-Wave dependencies remain pinned and cannot fully fail over.", "stat"),
                p("Unavailable entities", f'sum by (k8s_cluster) ({metric("homeassistant_entity_available")} == 0)', "short",
                  "Only an aggregate entity count is returned. No person, entity or camera identifiers are displayed."),
                p("MQTT broker", metric("homelab:mqtt_state"), "state",
                  "Whether the broker accepted the evidence collector's login, from each site, through its global Service. Message delivery between clients isn't measured.", "stat"),
                p("MQTT connected clients", metric("homelab_mqtt_clients_connected"), "short",
                  "From the broker's retained $SYS/broker/clients/connected, read by each site's collector. A sudden drop is clients disconnecting, not the broker restarting."),
            ],
        }, "Home", sections=True)
