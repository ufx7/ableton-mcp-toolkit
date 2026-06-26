# AbletonMCP Automation Server
# Schlanker Zusatz-MCP-Server für Envelope-Automation, Geräteparameter,
# Clip-Löschen und Track-Mute. Läuft parallel zum normalen AbletonMCP-Server
# und spricht mit demselben Remote Script (Port 9877).
#
# Config (claude_desktop_config.json):
#   "AbletonAutomation": {
#       "command": "uv",
#       "args": ["run", "--with", "mcp", "python",
#                "$HOME/Music/Ableton/User Library/AbletonMCP_Server/automation_server.py"]
#   }

from mcp.server.fastmcp import FastMCP, Context
import socket
import json
import logging
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AbletonAutomationServer")

HOST = "localhost"
PORT = 9877

_sock = None


def _connect():
    global _sock
    if _sock is not None:
        return _sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))
    _sock = s
    logger.info("Connected to Ableton Remote Script")
    return s


def _receive_full(sock, buffer_size=8192):
    chunks = []
    sock.settimeout(15.0)
    while True:
        chunk = sock.recv(buffer_size)
        if not chunk:
            if not chunks:
                raise Exception("Connection closed before receiving any data")
            break
        chunks.append(chunk)
        try:
            data = b''.join(chunks)
            json.loads(data.decode('utf-8'))
            return data
        except json.JSONDecodeError:
            continue
    return b''.join(chunks)


def send_command(command_type: str, params: Dict[str, Any] = None) -> Any:
    global _sock
    try:
        sock = _connect()
        command = {"type": command_type, "params": params or {}}
        sock.sendall(json.dumps(command).encode('utf-8'))
        data = _receive_full(sock)
        response = json.loads(data.decode('utf-8'))
        if response.get("status") == "error":
            raise Exception(response.get("message", "Unknown error from Ableton"))
        return response.get("result", {})
    except Exception:
        # Verbindung zurücksetzen, damit der nächste Call neu verbindet
        try:
            if _sock:
                _sock.close()
        except Exception:
            pass
        _sock = None
        raise


mcp = FastMCP("AbletonAutomation")


@mcp.tool()
def get_device_parameters(ctx: Context, track_index: int, device_index: int) -> str:
    """List all parameters of a device on a track (index, name, value, min, max).
    Use this first to find the parameter_index for set_clip_envelope or set_device_parameter.

    Parameters:
    - track_index: index of the track
    - device_index: index of the device on that track (see get_track_info)
    """
    try:
        result = send_command("get_device_parameters", {
            "track_index": track_index,
            "device_index": device_index
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error getting device parameters: {str(e)}"


@mcp.tool()
def set_clip_envelope(
    ctx: Context,
    track_index: int,
    clip_index: int,
    device_index: int,
    parameter_index: int,
    points: List[Dict[str, float]],
    domain: str = "arrangement",
    clear: bool = True,
    resolution: float = 0.25
) -> str:
    """Write an automation envelope for a device parameter into a clip.
    Values between points are linearly interpolated (pseudo-ramp via small steps).

    Parameters:
    - track_index: index of the track
    - clip_index: session slot index (domain='session') or arrangement clip index
      as returned by get_arrangement_clips (domain='arrangement')
    - device_index: device on the track
    - parameter_index: parameter of that device (use get_device_parameters first!)
    - points: list of {"time": beats_within_clip, "value": absolute_param_value}
      sorted by time. Values are clamped to the parameter's min/max.
    - domain: 'arrangement' (default) or 'session'
    - clear: clear existing envelope for this parameter first (default True)
    - resolution: step size in beats for ramps (default 0.25)
    """
    try:
        result = send_command("set_clip_envelope", {
            "track_index": track_index,
            "clip_index": clip_index,
            "domain": domain,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "points": points,
            "clear": clear,
            "resolution": resolution
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error setting clip envelope: {str(e)}"


@mcp.tool()
def get_clip_notes(ctx: Context, track_index: int, clip_index: int, domain: str = "session") -> str:
    """Read all MIDI notes from a clip (pitch, start_time, duration, velocity).

    Parameters:
    - track_index: index of the track
    - clip_index: session slot index or arrangement clip index
    - domain: 'session' (default) or 'arrangement'
    """
    try:
        result = send_command("get_clip_notes", {
            "track_index": track_index,
            "clip_index": clip_index,
            "domain": domain
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error getting clip notes: {str(e)}"


@mcp.tool()
def delete_arrangement_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """Delete a clip from the arrangement timeline.
    clip_index is the index in get_arrangement_clips (re-check after each delete,
    indices shift!).
    """
    try:
        result = send_command("delete_arrangement_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error deleting arrangement clip: {str(e)}"


@mcp.tool()
def delete_session_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """Delete a clip from a session view slot."""
    try:
        result = send_command("delete_session_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error deleting session clip: {str(e)}"


@mcp.tool()
def set_track_mute(ctx: Context, track_index: int, mute: bool) -> str:
    """Mute (true) or unmute (false) a track."""
    try:
        result = send_command("set_track_mute", {
            "track_index": track_index,
            "mute": mute
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error setting track mute: {str(e)}"


@mcp.tool()
def humanize_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    domain: str = "session",
    timing_ms: float = 10.0,
    velocity_amount: int = 12,
    exclude_pitches: List[int] = None,
    seed: int = None
) -> str:
    """Humanize a MIDI clip by jittering note timing and velocity, using the
    modern note API (notes are modified in place, not rewritten).

    Typical settings for a straight, driving techno feel:
    - Hi-hats: timing_ms=10, velocity_amount=12
    - Claps/snares: timing_ms=5, velocity_amount=8
    - Kick: leave straight -> either don't humanize that clip, or pass the
      kick's MIDI pitch in exclude_pitches so it stays untouched.

    Parameters:
    - track_index, clip_index: which clip (session slot or arrangement index)
    - domain: 'session' (default) or 'arrangement'
    - timing_ms: max +/- timing jitter in milliseconds (0 = leave timing)
    - velocity_amount: max +/- velocity offset, integer (0 = leave velocity)
    - exclude_pitches: list of MIDI pitches to leave completely untouched
      (e.g. [36] to protect a kick on C1 while humanizing the rest of the kit)
    - seed: optional int for reproducible randomization
    """
    try:
        result = send_command("humanize_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "domain": domain,
            "timing_ms": timing_ms,
            "velocity_amount": velocity_amount,
            "exclude_pitches": exclude_pitches,
            "seed": seed
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error humanizing clip: {str(e)}"


# ---- Generische LOM-Schicht (Fundament) ----
# Pfad-Syntax: ein Wurzelname plus Attribut-/Index-Schritte, z.B.
#   "song.tempo"
#   "song.tracks[3].name"
#   "song.tracks[3].clip_slots[2].clip"
#   "song.tracks[3].devices[0].parameters[35]"
# Wurzeln: song / live_set, app / application.
# Empfohlener Ablauf: describe_object -> list_children, um Ableton zu erkunden,
# dann gezielt get_property / set_property / call_method.

@mcp.tool()
def describe_object(ctx: Context, path: str, include_values: bool = True) -> str:
    """Introspect any object in Ableton's Live Object Model: list its
    readable properties (with current values), callable methods, and child
    collections. This is how you DISCOVER what is possible at a given path
    without prior knowledge of the API. Start at "song" and walk down.

    Parameters:
    - path: object path, e.g. "song" or "song.tracks[3].devices[0]"
    - include_values: also read each property's current value (default True)
    """
    try:
        result = send_command("describe_object", {"path": path, "include_values": include_values})
        return json.dumps(result, indent=2)
    except Exception as e:
        return "Error describing object: " + str(e)


@mcp.tool()
def list_children(ctx: Context, path: str) -> str:
    """List only the child collections of an object (name + length), e.g. the
    'tracks' of the song or the 'devices' of a track. Lighter than
    describe_object; use it to navigate the object tree cheaply.

    Parameters:
    - path: object path, e.g. "song" or "song.tracks[3]"
    """
    try:
        result = send_command("list_children", {"path": path})
        return json.dumps(result, indent=2)
    except Exception as e:
        return "Error listing children: " + str(e)


@mcp.tool()
def get_property(ctx: Context, path: str) -> str:
    """Read any property or object in the Live Object Model by path.
    Primitives are returned directly; objects as a short descriptor;
    collections as type + length (use list_children/describe_object for detail).

    Parameters:
    - path: e.g. "song.tempo", "song.tracks[3].name",
      "song.tracks[3].devices[0].parameters[35].value"
    """
    try:
        result = send_command("get_property", {"path": path})
        return json.dumps(result, indent=2)
    except Exception as e:
        return "Error getting property: " + str(e)


@mcp.tool()
def set_property(ctx: Context, path: str, value: Any) -> str:
    """Set a writable property in the Live Object Model by path.
    The path's last step must be a property name (e.g. ...name, ...tempo).
    To pass an Ableton object as the value, use {"__path__": "song.tracks[0]"}.

    Parameters:
    - path: e.g. "song.tempo" or "song.tracks[3].name"
    - value: the new value (number, string, bool) or a {"__path__": ...} object ref
    """
    try:
        result = send_command("set_property", {"path": path, "value": value})
        return json.dumps(result, indent=2)
    except Exception as e:
        return "Error setting property: " + str(e)


@mcp.tool()
def call_method(ctx: Context, path: str, method: str, args: List[Any] = None) -> str:
    """Call any method on a Live Object Model object by path.
    Use describe_object first to see which methods exist.
    Object arguments can be passed as {"__path__": "song.tracks[0]..."}.

    Parameters:
    - path: object the method belongs to, e.g. "song" or "song.view"
    - method: method name, e.g. "create_midi_track", "delete_track"
    - args: list of arguments (primitives, or {"__path__": ...} for object refs)
    """
    try:
        result = send_command("call_method", {"path": path, "method": method, "args": args or []})
        return json.dumps(result, indent=2)
    except Exception as e:
        return "Error calling method: " + str(e)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
