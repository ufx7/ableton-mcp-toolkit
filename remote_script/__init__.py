# AbletonMCP/init.py
from __future__ import absolute_import, print_function, unicode_literals

from _Framework.ControlSurface import ControlSurface
import socket
import json
import threading
import time
import traceback
import re
import random

# Change queue import for Python 2
try:
    import Queue as queue  # Python 2
except ImportError:
    import queue  # Python 3

# Constants for socket communication
DEFAULT_PORT = 9877
HOST = "localhost"

def create_instance(c_instance):
    """Create and return the AbletonMCP script instance"""
    return AbletonMCP(c_instance)

class AbletonMCP(ControlSurface):
    """AbletonMCP Remote Script for Ableton Live"""

    def __init__(self, c_instance):
        """Initialize the control surface"""
        ControlSurface.__init__(self, c_instance)
        self.log_message("AbletonMCP Remote Script initializing...")

        # Socket server for communication
        self.server = None
        self.client_threads = []
        self.server_thread = None
        self.running = False

        # Cache the song reference for easier access
        self._song = self.song()

        # Start the socket server
        self.start_server()

        self.log_message("AbletonMCP initialized")

        # Show a message in Ableton
        self.show_message("AbletonMCP: Listening for commands on port " + str(DEFAULT_PORT))

    def disconnect(self):
        """Called when Ableton closes or the control surface is removed"""
        self.log_message("AbletonMCP disconnecting...")
        self.running = False

        # Stop the server
        if self.server:
            try:
                self.server.close()
            except:
                pass

        # Wait for the server thread to exit
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(1.0)

        # Clean up any client threads
        for client_thread in self.client_threads[:]:
            if client_thread.is_alive():
                # We don't join them as they might be stuck
                self.log_message("Client thread still alive during disconnect")

        ControlSurface.disconnect(self)
        self.log_message("AbletonMCP disconnected")

    def start_server(self):
        """Start the socket server in a separate thread"""
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind((HOST, DEFAULT_PORT))
            self.server.listen(5)  # Allow up to 5 pending connections

            self.running = True
            self.server_thread = threading.Thread(target=self._server_thread)
            self.server_thread.daemon = True
            self.server_thread.start()

            self.log_message("Server started on port " + str(DEFAULT_PORT))
        except Exception as e:
            self.log_message("Error starting server: " + str(e))
            self.show_message("AbletonMCP: Error starting server - " + str(e))

    def _server_thread(self):
        """Server thread implementation - handles client connections"""
        try:
            self.log_message("Server thread started")
            # Set a timeout to allow regular checking of running flag
            self.server.settimeout(1.0)

            while self.running:
                try:
                    # Accept connections with timeout
                    client, address = self.server.accept()
                    self.log_message("Connection accepted from " + str(address))
                    self.show_message("AbletonMCP: Client connected")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()

                    # Keep track of client threads
                    self.client_threads.append(client_thread)

                    # Clean up finished client threads
                    self.client_threads = [t for t in self.client_threads if t.is_alive()]

                except socket.timeout:
                    # No connection yet, just continue
                    continue
                except Exception as e:
                    if self.running:  # Only log if still running
                        self.log_message("Server accept error: " + str(e))
                    time.sleep(0.5)

            self.log_message("Server thread stopped")
        except Exception as e:
            self.log_message("Server thread error: " + str(e))

    def _handle_client(self, client):
        """Handle communication with a connected client"""
        self.log_message("Client handler started")
        client.settimeout(None)  # No timeout for client socket
        buffer = ''  # Changed from b'' to '' for Python 2

        try:
            while self.running:
                try:
                    # Receive data
                    data = client.recv(8192)

                    if not data:
                        # Client disconnected
                        self.log_message("Client disconnected")
                        break

                    # Accumulate data in buffer with explicit encoding/decoding
                    try:
                        # Python 3: data is bytes, decode to string
                        buffer += data.decode('utf-8')
                    except AttributeError:
                        # Python 2: data is already string
                        buffer += data

                    try:
                        # Try to parse command from buffer
                        command = json.loads(buffer)  # Removed decode('utf-8')
                        buffer = ''  # Clear buffer after successful parse

                        self.log_message("Received command: " + str(command.get("type", "unknown")))

                        # Process the command and get response
                        response = self._process_command(command)

                        # Send the response with explicit encoding
                        try:
                            # Python 3: encode string to bytes
                            client.sendall(json.dumps(response).encode('utf-8'))
                        except AttributeError:
                            # Python 2: string is already bytes
                            client.sendall(json.dumps(response))
                    except ValueError:
                        # Incomplete data, wait for more
                        continue

                except Exception as e:
                    self.log_message("Error handling client data: " + str(e))
                    self.log_message(traceback.format_exc())

                    # Send error response if possible
                    error_response = {
                        "status": "error",
                        "message": str(e)
                    }
                    try:
                        # Python 3: encode string to bytes
                        client.sendall(json.dumps(error_response).encode('utf-8'))
                    except AttributeError:
                        # Python 2: string is already bytes
                        client.sendall(json.dumps(error_response))
                    except:
                        # If we can't send the error, the connection is probably dead
                        break

                    # For serious errors, break the loop
                    if not isinstance(e, ValueError):
                        break
        except Exception as e:
            self.log_message("Error in client handler: " + str(e))
        finally:
            try:
                client.close()
            except:
                pass
            self.log_message("Client handler stopped")

    def _process_command(self, command):
        """Process a command from the client and return a response"""
        command_type = command.get("type", "")
        params = command.get("params", {})

        # Immer gegen die AKTUELLE Session arbeiten. Beim Laden eines neuen
        # Live-Sets wird die zwischengespeicherte Song-Referenz ungueltig
        # (TPyHandle<ASong>-Fehler) -> hier bei jedem Befehl neu binden.
        try:
            self._song = self.song()
        except Exception:
            pass

        # Initialize response
        response = {
            "status": "success",
            "result": {}
        }

        try:
            # Route the command to the appropriate handler
            if command_type == "get_session_info":
                response["result"] = self._get_session_info()
            elif command_type == "get_track_info":
                track_index = params.get("track_index", 0)
                response["result"] = self._get_track_info(track_index)
            # Commands that modify Live's state should be scheduled on the main thread
            elif command_type == "get_arrangement_clips":
                track_index = params.get("track_index", 0)
                response["result"] = self._get_arrangement_clips(track_index)
            elif command_type == "get_clip_notes":
                response["result"] = self._get_clip_notes(
                    params.get("track_index", 0),
                    params.get("clip_index", 0),
                    params.get("domain", "session"))
            elif command_type == "get_device_parameters":
                response["result"] = self._get_device_parameters(
                    params.get("track_index", 0),
                    params.get("device_index", 0))
            # ---- Generische LOM-Lesebefehle (Fundament-Schicht) ----
            elif command_type == "get_property":
                response["result"] = self._get_property(params.get("path"))
            elif command_type == "describe_object":
                response["result"] = self._describe_object(
                    params.get("path"),
                    params.get("include_values", True))
            elif command_type == "list_children":
                response["result"] = self._list_children(params.get("path"))
            elif command_type in ["create_midi_track", "set_track_name",
                                 "create_clip", "add_notes_to_clip", "set_clip_name",
                                 "set_tempo", "fire_clip", "stop_clip",
                                 "start_playback", "stop_playback", "load_browser_item",
                                 "duplicate_to_arrangement", "duplicate_session_clip_to_arrangement",
                                 "duplicate_clip_to_arrangement",
                                 "set_arrangement_time", "set_song_time", "set_current_song_time",
                                 "jump_to_time",
                                 "switch_to_arrangement_view", "show_arrangement_view",
                                 "focus_arrangement_view",
                                 "set_clip_envelope", "delete_arrangement_clip",
                                 "delete_session_clip", "set_track_mute",
                                 "set_property", "call_method",
                                 "humanize_clip"]:
                # Use a thread-safe approach with a response queue
                response_queue = queue.Queue()

                # Define a function to execute on the main thread
                def main_thread_task():
                    try:
                        result = None
                        if command_type == "create_midi_track":
                            index = params.get("index", -1)
                            result = self._create_midi_track(index)
                        elif command_type == "set_track_name":
                            track_index = params.get("track_index", 0)
                            name = params.get("name", "")
                            result = self._set_track_name(track_index, name)
                        elif command_type == "create_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            length = params.get("length", 4.0)
                            result = self._create_clip(track_index, clip_index, length)
                        elif command_type == "add_notes_to_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            notes = params.get("notes", [])
                            result = self._add_notes_to_clip(track_index, clip_index, notes)
                        elif command_type == "set_clip_name":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            name = params.get("name", "")
                            result = self._set_clip_name(track_index, clip_index, name)
                        elif command_type == "set_tempo":
                            tempo = params.get("tempo", 120.0)
                            result = self._set_tempo(tempo)
                        elif command_type == "fire_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            result = self._fire_clip(track_index, clip_index)
                        elif command_type == "stop_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            result = self._stop_clip(track_index, clip_index)
                        elif command_type == "start_playback":
                            result = self._start_playback()
                        elif command_type == "stop_playback":
                            result = self._stop_playback()
                        elif command_type == "load_instrument_or_effect":
                            track_index = params.get("track_index", 0)
                            uri = params.get("uri", "")
                            result = self._load_instrument_or_effect(track_index, uri)
                        elif command_type == "load_browser_item":
                            track_index = params.get("track_index", 0)
                            item_uri = params.get("item_uri", "")
                            result = self._load_browser_item(track_index, item_uri)
                        elif command_type in ("duplicate_to_arrangement", "duplicate_session_clip_to_arrangement", "duplicate_clip_to_arrangement"):
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            destination_time = params.get("destination_time", params.get("time", params.get("position", 0.0)))
                            result = self._duplicate_to_arrangement(track_index, clip_index, destination_time)
                        elif command_type in ("set_arrangement_time", "set_song_time", "set_current_song_time", "jump_to_time"):
                            time_pos = params.get("time", params.get("position", params.get("beat_time", params.get("destination_time", 0.0))))
                            result = self._set_arrangement_time(time_pos)
                        elif command_type in ("switch_to_arrangement_view", "show_arrangement_view", "focus_arrangement_view"):
                            result = self._switch_to_arrangement_view()
                        elif command_type == "set_clip_envelope":
                            result = self._set_clip_envelope(
                                params.get("track_index", 0),
                                params.get("clip_index", 0),
                                params.get("domain", "session"),
                                params.get("device_index", 0),
                                params.get("parameter_index", 0),
                                params.get("points", []),
                                params.get("clear", True),
                                params.get("resolution", 0.25))
                        elif command_type == "delete_arrangement_clip":
                            result = self._delete_arrangement_clip(
                                params.get("track_index", 0),
                                params.get("clip_index", 0))
                        elif command_type == "delete_session_clip":
                            result = self._delete_session_clip(
                                params.get("track_index", 0),
                                params.get("clip_index", 0))
                        elif command_type == "set_track_mute":
                            result = self._set_track_mute(
                                params.get("track_index", 0),
                                params.get("mute", True))
                        elif command_type == "set_property":
                            result = self._set_property(
                                params.get("path"),
                                params.get("value"))
                        elif command_type == "call_method":
                            result = self._call_method(
                                params.get("path"),
                                params.get("method"),
                                params.get("args", []))
                        elif command_type == "humanize_clip":
                            result = self._humanize_clip(
                                params.get("track_index", 0),
                                params.get("clip_index", 0),
                                params.get("domain", "session"),
                                params.get("timing_ms", 10.0),
                                params.get("velocity_amount", 12),
                                params.get("exclude_pitches", None),
                                params.get("seed", None))

                        # Put the result in the queue
                        response_queue.put({"status": "success", "result": result})
                    except Exception as e:
                        self.log_message("Error in main thread task: " + str(e))
                        self.log_message(traceback.format_exc())
                        response_queue.put({"status": "error", "message": str(e)})

                # Schedule the task to run on the main thread
                try:
                    self.schedule_message(0, main_thread_task)
                except AssertionError:
                    # If we're already on the main thread, execute directly
                    main_thread_task()

                # Wait for the response with a timeout
                try:
                    task_response = response_queue.get(timeout=10.0)
                    if task_response.get("status") == "error":
                        response["status"] = "error"
                        response["message"] = task_response.get("message", "Unknown error")
                    else:
                        response["result"] = task_response.get("result", {})
                except queue.Empty:
                    response["status"] = "error"
                    response["message"] = "Timeout waiting for operation to complete"
            elif command_type == "get_browser_item":
                uri = params.get("uri", None)
                path = params.get("path", None)
                response["result"] = self._get_browser_item(uri, path)
            elif command_type == "get_browser_categories":
                category_type = params.get("category_type", "all")
                response["result"] = self._get_browser_categories(category_type)
            elif command_type == "get_browser_items":
                path = params.get("path", "")
                item_type = params.get("item_type", "all")
                response["result"] = self._get_browser_items(path, item_type)
            # Add the new browser commands
            elif command_type == "get_browser_tree":
                category_type = params.get("category_type", "all")
                response["result"] = self.get_browser_tree(category_type)
            elif command_type == "get_browser_items_at_path":
                path = params.get("path", "")
                response["result"] = self.get_browser_items_at_path(path)
            else:
                response["status"] = "error"
                response["message"] = "Unknown command: " + command_type
        except Exception as e:
            self.log_message("Error processing command: " + str(e))
            self.log_message(traceback.format_exc())
            response["status"] = "error"
            response["message"] = str(e)

        return response

    # ---- Generischer LOM-Zugang (Fundament-Schicht) ----
    # Pfad-basierte Adressierung, z.B. "song.tracks[3].clip_slots[2].clip"
    # oder "song.tracks[3].devices[0].parameters[35]".
    # Wurzeln: song / live_set, app / application.

    def _path_roots(self):
        roots = {"song": self._song, "live_set": self._song}
        try:
            app = self.application()
            roots["app"] = app
            roots["application"] = app
        except Exception:
            pass
        return roots

    def _split_path_steps(self, path):
        if path is None:
            raise Exception("path is required")
        steps = re.findall(r'([A-Za-z_]\w*)|\[\s*(-?\d+)\s*\]', str(path).strip())
        if not steps:
            raise Exception("Could not parse path: " + str(path))
        return steps

    def _walk_steps(self, steps):
        first_name = steps[0][0]
        if not first_name:
            raise Exception("Path must start with a root name (e.g. 'song')")
        roots = self._path_roots()
        key = first_name.lower()
        if key not in roots:
            raise Exception("Unknown root '" + first_name + "'. Known roots: " +
                            ", ".join(sorted(roots.keys())))
        obj = roots[key]
        walked = first_name
        for name, index in steps[1:]:
            if name:
                if not hasattr(obj, name):
                    raise Exception("'" + name + "' not found on " + walked +
                                    " (" + self._lom_type_name(obj) + ")")
                obj = getattr(obj, name)
                walked = walked + "." + name
            else:
                try:
                    obj = obj[int(index)]
                except Exception as e:
                    raise Exception("Index [" + str(index) + "] failed on " +
                                    walked + ": " + str(e))
                walked = walked + "[" + str(index) + "]"
        return obj

    def _resolve_path(self, path):
        return self._walk_steps(self._split_path_steps(path))

    def _lom_type_name(self, obj):
        try:
            return type(obj).__name__
        except Exception:
            return "object"

    def _is_primitive(self, v):
        return (v is None or isinstance(v, bool) or isinstance(v, int)
                or isinstance(v, float) or isinstance(v, str))

    def _is_collection(self, v):
        if isinstance(v, (str, bytes, dict)):
            return False
        if isinstance(v, (list, tuple)):
            return True
        return hasattr(v, '__len__') and hasattr(v, '__getitem__')

    def _serialize_lom_value(self, value):
        """JSON-sichere, token-sparsame Darstellung eines beliebigen LOM-Werts."""
        if self._is_primitive(value):
            return value
        if isinstance(value, bytes):
            try:
                return value.decode('utf-8', 'replace')
            except Exception:
                return str(value)
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                out[str(k)] = self._serialize_lom_value(v)
            return out
        if self._is_collection(value):
            try:
                items = list(value)
            except Exception:
                return {"__collection__": self._lom_type_name(value)}
            # Kleine, rein primitive Listen inline; sonst nur Typ + Länge
            if len(items) <= 64 and all(self._is_primitive(i) for i in items):
                return [self._serialize_lom_value(i) for i in items]
            return {"__collection__": self._lom_type_name(value), "length": len(items)}
        # LOM-Objekt: kurzer Deskriptor (nicht rekursiv aufklappen)
        desc = {"__object__": self._lom_type_name(value)}
        got = False
        for attr in ("name", "value"):
            try:
                av = getattr(value, attr)
                if self._is_primitive(av):
                    desc[attr] = av
                    got = True
            except Exception:
                pass
        if not got:
            try:
                s = str(value)
                if s and len(s) < 200:
                    desc["repr"] = s
            except Exception:
                pass
        return desc

    def _get_property(self, path):
        obj = self._resolve_path(path)
        return {"path": path, "type": self._lom_type_name(obj),
                "value": self._serialize_lom_value(obj)}

    def _list_children(self, path):
        obj = self._resolve_path(path)
        children = []
        for name in sorted(set(dir(obj))):
            if name.startswith('_'):
                continue
            try:
                attr = getattr(obj, name)
            except Exception:
                continue
            if self._is_collection(attr):
                try:
                    children.append({"name": name, "length": len(attr)})
                except Exception:
                    children.append({"name": name})
        return {"path": path, "type": self._lom_type_name(obj), "children": children}

    def _describe_object(self, path, include_values=True):
        obj = self._resolve_path(path)
        props = {}
        methods = []
        children = []
        for name in sorted(set(dir(obj))):
            if name.startswith('_'):
                continue
            try:
                attr = getattr(obj, name)
            except Exception:
                continue
            if callable(attr):
                methods.append(name)
            elif self._is_collection(attr):
                try:
                    children.append({"name": name, "length": len(attr)})
                except Exception:
                    children.append({"name": name})
            else:
                if include_values:
                    try:
                        props[name] = self._serialize_lom_value(attr)
                    except Exception:
                        props[name] = {"__error__": "unreadable"}
                else:
                    props[name] = None
        return {"path": path, "type": self._lom_type_name(obj),
                "properties": props, "methods": methods, "children": children}

    def _coerce_arg(self, a):
        # Erlaubt Objekt-Argumente per Pfad: {"__path__": "song.tracks[0]..."}
        if isinstance(a, dict) and "__path__" in a:
            return self._resolve_path(a["__path__"])
        return a

    def _set_property(self, path, value):
        steps = self._split_path_steps(path)
        if len(steps) < 2:
            raise Exception("set_property needs at least a root and a property, e.g. 'song.tempo'")
        parent = self._walk_steps(steps[:-1])
        name, index = steps[-1]
        value = self._coerce_arg(value)
        # FastMCP serialisiert Any-Parameter als str -> typ-gefuehrt zurueckkoerzieren.
        # Wir lesen den Typ der Ziel-Property: ein String-Property (z.B. .name) bleibt
        # String; 'true'/'false' wird Bool (auch fuer int-0/1-Properties wie arm/mute/solo);
        # sonst int/float. (Pauschale Koerzion wuerde Namen wie "123" faelschlich zu Zahlen.)
        if isinstance(value, str) and name:
            try:
                current = getattr(parent, name)
            except Exception:
                current = None
            low = value.strip().lower()
            if isinstance(current, str):
                pass  # String-Property -> value bleibt String
            elif low in ('true', 'false'):
                value = (low == 'true')
            else:
                try:
                    value = int(value)
                except ValueError:
                    try:
                        value = float(value)
                    except ValueError:
                        pass  # echter String bleibt String
        if name:
            setattr(parent, name, value)
            try:
                return {"path": path,
                        "new_value": self._serialize_lom_value(getattr(parent, name))}
            except Exception:
                return {"path": path, "new_value": value}
        else:
            parent[int(index)] = value
            return {"path": path, "new_value": value}

    def _call_method(self, path, method, args):
        if not method:
            raise Exception("method name is required")
        obj = self._resolve_path(path)
        if not hasattr(obj, method):
            raise Exception("'" + method + "' not found on " + self._lom_type_name(obj))
        fn = getattr(obj, method)
        if not callable(fn):
            raise Exception("'" + method + "' on " + self._lom_type_name(obj) + " is not callable")
        coerced = [self._coerce_arg(a) for a in (args or [])]
        # set_notes erwartet tuple of tuples (Live-API, JSON hat nur lists)
        if method == "set_notes" and coerced and isinstance(coerced[0], list):
            coerced[0] = tuple(tuple(n) for n in coerced[0])
        result = fn(*coerced)
        return {"path": path, "method": method,
                "result": self._serialize_lom_value(result)}

    # Command implementations

    def _get_session_info(self):
        """Get information about the current session"""
        try:
            result = {
                "tempo": self._song.tempo,
                "signature_numerator": self._song.signature_numerator,
                "signature_denominator": self._song.signature_denominator,
                "track_count": len(self._song.tracks),
                "return_track_count": len(self._song.return_tracks),
                "master_track": {
                    "name": "Master",
                    "volume": self._song.master_track.mixer_device.volume.value,
                    "panning": self._song.master_track.mixer_device.panning.value
                }
            }
            return result
        except Exception as e:
            self.log_message("Error getting session info: " + str(e))
            raise

    def _get_track_info(self, track_index):
        """Get information about a track"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            # Get clip slots
            clip_slots = []
            for slot_index, slot in enumerate(track.clip_slots):
                clip_info = None
                if slot.has_clip:
                    clip = slot.clip
                    clip_info = {
                        "name": clip.name,
                        "length": clip.length,
                        "is_playing": clip.is_playing,
                        "is_recording": clip.is_recording
                    }

                clip_slots.append({
                    "index": slot_index,
                    "has_clip": slot.has_clip,
                    "clip": clip_info
                })

            # Get devices
            devices = []
            for device_index, device in enumerate(track.devices):
                devices.append({
                    "index": device_index,
                    "name": device.name,
                    "class_name": device.class_name,
                    "type": self._get_device_type(device)
                })

            result = {
                "index": track_index,
                "name": track.name,
                "is_audio_track": track.has_audio_input,
                "is_midi_track": track.has_midi_input,
                "mute": track.mute,
                "solo": track.solo,
                "arm": track.arm,
                "volume": track.mixer_device.volume.value,
                "panning": track.mixer_device.panning.value,
                "clip_slots": clip_slots,
                "devices": devices
            }
            return result
        except Exception as e:
            self.log_message("Error getting track info: " + str(e))
            raise

    def _create_midi_track(self, index):
        """Create a new MIDI track at the specified index"""
        try:
            # Create the track
            self._song.create_midi_track(index)

            # Get the new track
            new_track_index = len(self._song.tracks) - 1 if index == -1 else index
            new_track = self._song.tracks[new_track_index]

            result = {
                "index": new_track_index,
                "name": new_track.name
            }
            return result
        except Exception as e:
            self.log_message("Error creating MIDI track: " + str(e))
            raise


    def _set_track_name(self, track_index, name):
        """Set the name of a track"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            # Set the name
            track = self._song.tracks[track_index]
            track.name = name

            result = {
                "name": track.name
            }
            return result
        except Exception as e:
            self.log_message("Error setting track name: " + str(e))
            raise

    def _create_clip(self, track_index, clip_index, length):
        """Create a new MIDI clip in the specified track and clip slot"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            # Check if the clip slot already has a clip
            if clip_slot.has_clip:
                raise Exception("Clip slot already has a clip")

            # Create the clip
            clip_slot.create_clip(length)

            result = {
                "name": clip_slot.clip.name,
                "length": clip_slot.clip.length
            }
            return result
        except Exception as e:
            self.log_message("Error creating clip: " + str(e))
            raise

    def _add_notes_to_clip(self, track_index, clip_index, notes):
        """Add MIDI notes to a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            if not clip_slot.has_clip:
                raise Exception("No clip in slot")

            clip = clip_slot.clip

            # Convert note data to Live's format
            live_notes = []
            for note in notes:
                pitch = note.get("pitch", 60)
                start_time = note.get("start_time", 0.0)
                duration = note.get("duration", 0.25)
                velocity = note.get("velocity", 100)
                mute = note.get("mute", False)

                live_notes.append((pitch, start_time, duration, velocity, mute))

            # Add the notes
            clip.set_notes(tuple(live_notes))

            result = {
                "note_count": len(notes)
            }
            return result
        except Exception as e:
            self.log_message("Error adding notes to clip: " + str(e))
            raise

    def _set_clip_name(self, track_index, clip_index, name):
        """Set the name of a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            if not clip_slot.has_clip:
                raise Exception("No clip in slot")

            clip = clip_slot.clip
            clip.name = name

            result = {
                "name": clip.name
            }
            return result
        except Exception as e:
            self.log_message("Error setting clip name: " + str(e))
            raise

    def _set_tempo(self, tempo):
        """Set the tempo of the session"""
        try:
            self._song.tempo = tempo

            result = {
                "tempo": self._song.tempo
            }
            return result
        except Exception as e:
            self.log_message("Error setting tempo: " + str(e))
            raise

    def _fire_clip(self, track_index, clip_index):
        """Fire a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            if not clip_slot.has_clip:
                raise Exception("No clip in slot")

            clip_slot.fire()

            result = {
                "fired": True
            }
            return result
        except Exception as e:
            self.log_message("Error firing clip: " + str(e))
            raise

    def _stop_clip(self, track_index, clip_index):
        """Stop a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            clip_slot.stop()

            result = {
                "stopped": True
            }
            return result
        except Exception as e:
            self.log_message("Error stopping clip: " + str(e))
            raise


    def _start_playback(self):
        """Start playing the session"""
        try:
            self._song.start_playing()

            result = {
                "playing": self._song.is_playing
            }
            return result
        except Exception as e:
            self.log_message("Error starting playback: " + str(e))
            raise

    def _stop_playback(self):
        """Stop playing the session"""
        try:
            self._song.stop_playing()

            result = {
                "playing": self._song.is_playing
            }
            return result
        except Exception as e:
            self.log_message("Error stopping playback: " + str(e))
            raise

    def _resolve_clip(self, track_index, clip_index, domain="session"):
        """Resolve a clip object from session slot or arrangement position"""
        if track_index < 0 or track_index >= len(self._song.tracks):
            raise IndexError("Track index out of range")
        track = self._song.tracks[track_index]
        if domain == "arrangement":
            if not hasattr(track, 'arrangement_clips'):
                raise Exception("arrangement_clips not available (requires Live 11+)")
            clips = list(track.arrangement_clips)
            if clip_index < 0 or clip_index >= len(clips):
                raise IndexError("Arrangement clip index out of range")
            return clips[clip_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        slot = track.clip_slots[clip_index]
        if not slot.has_clip:
            raise Exception("No clip in slot")
        return slot.clip

    def _get_clip_notes(self, track_index, clip_index, domain="session"):
        """Read all MIDI notes from a clip (session or arrangement)"""
        try:
            clip = self._resolve_clip(track_index, clip_index, domain)
            if not clip.is_midi_clip:
                raise Exception("Not a MIDI clip")
            notes = []
            if hasattr(clip, 'get_notes_extended'):
                vec = clip.get_notes_extended(0, 128, 0.0, float(clip.length))
                for n in vec:
                    notes.append({
                        "note_id": n.note_id,
                        "pitch": n.pitch,
                        "start_time": n.start_time,
                        "duration": n.duration,
                        "velocity": n.velocity,
                        "mute": n.mute
                    })
            else:
                raw = clip.get_notes(0.0, 0, float(clip.length), 128)
                for n in raw:
                    notes.append({
                        "pitch": n[0],
                        "start_time": n[1],
                        "duration": n[2],
                        "velocity": n[3],
                        "mute": n[4]
                    })
            return {
                "track_index": track_index,
                "clip_index": clip_index,
                "domain": domain,
                "clip_name": clip.name,
                "clip_length": clip.length,
                "note_count": len(notes),
                "notes": notes
            }
        except Exception as e:
            self.log_message("Error getting clip notes: " + str(e))
            raise

    def _humanize_clip(self, track_index, clip_index, domain="session",
                       timing_ms=10.0, velocity_amount=12,
                       exclude_pitches=None, seed=None):
        """Humanize a MIDI clip by jittering note timing and velocity.
        Uses the modern note API (get_notes_extended + apply_note_modifications),
        i.e. existing notes are modified in place, not rewritten.

        - timing_ms: max +/- timing jitter in milliseconds (converted to beats
          via the current tempo). 0 = leave timing untouched.
        - velocity_amount: max +/- velocity offset (integer). 0 = leave velocity.
        - exclude_pitches: list of MIDI pitches to leave completely untouched
          (e.g. the kick pad, so straight elements stay straight).
        - seed: optional int for reproducible randomization.
        """
        clip = self._resolve_clip(track_index, clip_index, domain)
        if not clip.is_midi_clip:
            raise Exception("Not a MIDI clip")
        if not hasattr(clip, 'get_notes_extended') or not hasattr(clip, 'apply_note_modifications'):
            raise Exception("Modern note API not available (requires Live 11+)")

        rng = random.Random(seed)
        exclude = set(exclude_pitches or [])
        tempo = float(self._song.tempo)
        # ms -> beats: beats = ms/1000 * (tempo/60)
        beats_per_ms = tempo / 60.0 / 1000.0
        max_beats = float(timing_ms) * beats_per_ms
        vel_amt = int(velocity_amount or 0)

        vec = clip.get_notes_extended(0, 128, 0.0, float(clip.length))
        modified = 0
        skipped = 0
        for n in vec:
            if n.pitch in exclude:
                skipped += 1
                continue
            if max_beats > 0:
                new_start = n.start_time + rng.uniform(-max_beats, max_beats)
                if new_start < 0.0:
                    new_start = 0.0
                n.start_time = new_start
            if vel_amt > 0:
                nv = n.velocity + rng.randint(-vel_amt, vel_amt)
                if nv < 1:
                    nv = 1
                elif nv > 127:
                    nv = 127
                n.velocity = nv
            modified += 1

        clip.apply_note_modifications(vec)
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "domain": domain,
            "clip_name": clip.name,
            "modified": modified,
            "skipped_excluded": skipped,
            "timing_ms": timing_ms,
            "velocity_amount": vel_amt,
            "tempo": tempo
        }

    def _get_device_parameters(self, track_index, device_index):
        """List all parameters of a device with index, name, value, min, max"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            track = self._song.tracks[track_index]
            if device_index < 0 or device_index >= len(track.devices):
                raise IndexError("Device index out of range")
            device = track.devices[device_index]
            parameters = []
            for i, p in enumerate(device.parameters):
                parameters.append({
                    "index": i,
                    "name": p.name,
                    "value": p.value,
                    "min": p.min,
                    "max": p.max,
                    "is_quantized": p.is_quantized,
                    "display_value": str(p.str_for_value(p.value)) if hasattr(p, 'str_for_value') else str(p.value)
                })
            return {
                "track_index": track_index,
                "device_index": device_index,
                "device_name": device.name,
                "parameter_count": len(parameters),
                "parameters": parameters
            }
        except Exception as e:
            self.log_message("Error getting device parameters: " + str(e))
            raise

    def _set_clip_envelope(self, track_index, clip_index, domain, device_index, parameter_index, points, clear=True, resolution=0.25):
        """Write an automation envelope for a device parameter into a clip.
        points: list of {time, value} sorted by time; values are interpolated
        linearly between points using small steps of `resolution` beats."""
        try:
            clip = self._resolve_clip(track_index, clip_index, domain)
            track = self._song.tracks[track_index]
            if device_index < 0 or device_index >= len(track.devices):
                raise IndexError("Device index out of range")
            device = track.devices[device_index]
            if parameter_index < 0 or parameter_index >= len(device.parameters):
                raise IndexError("Parameter index out of range")
            param = device.parameters[parameter_index]

            if not points:
                raise Exception("No points provided")

            if clear and hasattr(clip, 'clear_envelope'):
                try:
                    clip.clear_envelope(param)
                except Exception:
                    pass

            env = None
            if hasattr(clip, 'automation_envelope'):
                env = clip.automation_envelope(param)
            if env is None and hasattr(clip, 'create_automation_envelope'):
                env = clip.create_automation_envelope(param)
            if env is None:
                raise Exception("Could not create automation envelope (clip envelopes not available)")

            def clamp(v):
                return max(param.min, min(param.max, float(v)))

            pts = sorted(points, key=lambda p: float(p.get("time", 0.0)))
            steps_written = 0
            for i in range(len(pts)):
                t0 = float(pts[i].get("time", 0.0))
                v0 = clamp(pts[i].get("value", 0.0))
                if i + 1 < len(pts):
                    t1 = float(pts[i + 1].get("time", 0.0))
                    v1 = clamp(pts[i + 1].get("value", 0.0))
                    if t1 <= t0:
                        continue
                    # subdivide segment into steps for a pseudo-ramp
                    t = t0
                    while t < t1:
                        span = min(resolution, t1 - t)
                        frac = (t - t0) / (t1 - t0)
                        v = v0 + (v1 - v0) * frac
                        env.insert_step(t, span, v)
                        steps_written += 1
                        t += span
                else:
                    env.insert_step(t0, 0.0, v0)
                    steps_written += 1

            return {
                "track_index": track_index,
                "clip_index": clip_index,
                "domain": domain,
                "parameter": param.name,
                "points": len(pts),
                "steps_written": steps_written
            }
        except Exception as e:
            self.log_message("Error setting clip envelope: " + str(e))
            self.log_message(traceback.format_exc())
            raise

    def _delete_arrangement_clip(self, track_index, clip_index):
        """Delete a clip from the arrangement timeline"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            track = self._song.tracks[track_index]
            if not hasattr(track, 'arrangement_clips'):
                raise Exception("arrangement_clips not available")
            clips = list(track.arrangement_clips)
            if clip_index < 0 or clip_index >= len(clips):
                raise IndexError("Arrangement clip index out of range")
            clip = clips[clip_index]
            name = clip.name
            start = clip.start_time
            if not hasattr(track, 'delete_clip'):
                raise Exception("delete_clip not available (requires Live 11+)")
            track.delete_clip(clip)
            return {"deleted": True, "clip_name": name, "start_time": start}
        except Exception as e:
            self.log_message("Error deleting arrangement clip: " + str(e))
            raise

    def _delete_session_clip(self, track_index, clip_index):
        """Delete a clip from a session slot"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            track = self._song.tracks[track_index]
            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")
            slot = track.clip_slots[clip_index]
            if not slot.has_clip:
                raise Exception("No clip in slot")
            name = slot.clip.name
            slot.delete_clip()
            return {"deleted": True, "clip_name": name}
        except Exception as e:
            self.log_message("Error deleting session clip: " + str(e))
            raise

    def _set_track_mute(self, track_index, mute):
        """Mute or unmute a track"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            track = self._song.tracks[track_index]
            track.mute = bool(mute)
            return {"track_name": track.name, "mute": track.mute}
        except Exception as e:
            self.log_message("Error setting track mute: " + str(e))
            raise

    def _get_arrangement_clips(self, track_index):
        """List all clips in the Arrangement timeline for a track"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if not hasattr(track, 'arrangement_clips'):
                raise Exception("arrangement_clips not available (requires Live 11+)")

            clips = []
            for clip in track.arrangement_clips:
                clip_info = {
                    "name": clip.name if hasattr(clip, 'name') else "",
                    "start_time": clip.start_time if hasattr(clip, 'start_time') else None,
                    "end_time": clip.end_time if hasattr(clip, 'end_time') else None,
                    "length": clip.length if hasattr(clip, 'length') else None,
                    "type": "audio" if (hasattr(clip, 'is_audio_clip') and clip.is_audio_clip) else "midi",
                    "muted": clip.muted if hasattr(clip, 'muted') else False
                }
                clips.append(clip_info)

            result = {
                "track_index": track_index,
                "track_name": track.name,
                "clip_count": len(clips),
                "clips": clips
            }
            return result
        except Exception as e:
            self.log_message("Error getting arrangement clips: " + str(e))
            raise

    def _duplicate_to_arrangement(self, track_index, clip_index, destination_time):
        """Copy a Session clip into the Arrangement at destination_time (beats)"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            if not clip_slot.has_clip:
                raise Exception("No clip in slot")

            if not hasattr(track, 'duplicate_clip_to_arrangement'):
                raise Exception("duplicate_clip_to_arrangement not available (requires Live 11+)")

            new_clip = track.duplicate_clip_to_arrangement(clip_slot.clip, destination_time)

            result = {
                "duplicated": True,
                "track_index": track_index,
                "clip_name": clip_slot.clip.name,
                "destination_time": destination_time
            }
            if new_clip is not None and hasattr(new_clip, 'start_time'):
                result["start_time"] = new_clip.start_time
                result["end_time"] = new_clip.end_time
            return result
        except Exception as e:
            self.log_message("Error duplicating clip to arrangement: " + str(e))
            raise

    def _set_arrangement_time(self, time_pos):
        """Set the arrangement playhead position (in beats)"""
        try:
            self._song.current_song_time = float(time_pos)
            result = {
                "current_song_time": self._song.current_song_time
            }
            return result
        except Exception as e:
            self.log_message("Error setting arrangement time: " + str(e))
            raise

    def _switch_to_arrangement_view(self):
        """Switch Live's main view to the Arranger"""
        try:
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")
            app.view.show_view("Arranger")
            result = {
                "view": "Arranger"
            }
            return result
        except Exception as e:
            self.log_message("Error switching to arrangement view: " + str(e))
            raise

    def _get_browser_item(self, uri, path):
        """Get a browser item by URI or path"""
        try:
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")

            result = {
                "uri": uri,
                "path": path,
                "found": False
            }

            # Try to find by URI first if provided
            if uri:
                item = self._find_browser_item_by_uri(app.browser, uri)
                if item:
                    result["found"] = True
                    result["item"] = {
                        "name": item.name,
                        "is_folder": item.is_folder,
                        "is_device": item.is_device,
                        "is_loadable": item.is_loadable,
                        "uri": item.uri
                    }
                    return result

            # If URI not provided or not found, try by path
            if path:
                # Parse the path and navigate to the specified item
                path_parts = path.split("/")

                # Determine the root based on the first part
                current_item = None
                if path_parts[0].lower() == "nstruments":
                    current_item = app.browser.instruments
                elif path_parts[0].lower() == "sounds":
                    current_item = app.browser.sounds
                elif path_parts[0].lower() == "drums":
                    current_item = app.browser.drums
                elif path_parts[0].lower() == "audio_effects":
                    current_item = app.browser.audio_effects
                elif path_parts[0].lower() == "midi_effects":
                    current_item = app.browser.midi_effects
                else:
                    # Default to instruments if not specified
                    current_item = app.browser.instruments
                    # Don't skip the first part in this case
                    path_parts = ["instruments"] + path_parts

                # Navigate through the path
                for i in range(1, len(path_parts)):
                    part = path_parts[i]
                    if not part:  # Skip empty parts
                        continue

                    found = False
                    for child in current_item.children:
                        if child.name.lower() == part.lower():
                            current_item = child
                            found = True
                            break

                    if not found:
                        result["error"] = "Path part '{0}' not found".format(part)
                        return result

                # Found the item
                result["found"] = True
                result["item"] = {
                    "name": current_item.name,
                    "is_folder": current_item.is_folder,
                    "is_device": current_item.is_device,
                    "is_loadable": current_item.is_loadable,
                    "uri": current_item.uri
                }

            return result
        except Exception as e:
            self.log_message("Error getting browser item: " + str(e))
            self.log_message(traceback.format_exc())
            raise



    def _load_browser_item(self, track_index, item_uri):
        """Load a browser item onto a track by its URI"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            # Access the application's browser instance instead of creating a new one
            app = self.application()

            # Find the browser item by URI
            item = self._find_browser_item_by_uri(app.browser, item_uri)

            if not item:
                raise ValueError("Browser item with URI '{0}' not found".format(item_uri))

            # Select the track
            self._song.view.selected_track = track

            # Load the item
            app.browser.load_item(item)

            result = {
                "loaded": True,
                "item_name": item.name,
                "track_name": track.name,
                "uri": item_uri
            }
            return result
        except Exception as e:
            self.log_message("Error loading browser item: {0}".format(str(e)))
            self.log_message(traceback.format_exc())
            raise

    def _find_browser_item_by_uri(self, browser_or_item, uri, max_depth=10, current_depth=0):
        """Find a browser item by its URI"""
        try:
            # Check if this is the item we're looking for
            if hasattr(browser_or_item, 'uri') and browser_or_item.uri == uri:
                return browser_or_item

            # Stop recursion if we've reached max depth
            if current_depth >= max_depth:
                return None

            # Check if this is a browser with root categories
            if hasattr(browser_or_item, 'instruments'):
                # Check all main categories
                categories = [
                    browser_or_item.instruments,
                    browser_or_item.sounds,
                    browser_or_item.drums,
                    browser_or_item.audio_effects,
                    browser_or_item.midi_effects
                ]

                for category in categories:
                    item = self._find_browser_item_by_uri(category, uri, max_depth, current_depth + 1)
                    if item:
                        return item

                return None

            # Check if this item has children
            if hasattr(browser_or_item, 'children') and browser_or_item.children:
                for child in browser_or_item.children:
                    item = self._find_browser_item_by_uri(child, uri, max_depth, current_depth + 1)
                    if item:
                        return item

            return None
        except Exception as e:
            self.log_message("Error finding browser item by URI: {0}".format(str(e)))
            return None

    # Helper methods

    def _get_device_type(self, device):
        """Get the type of a device"""
        try:
            # Simple heuristic - in a real implementation you'd look at the device class
            if device.can_have_drum_pads:
                return "drum_machine"
            elif device.can_have_chains:
                return "rack"
            elif "instrument" in device.class_display_name.lower():
                return "instrument"
            elif "audio_effect" in device.class_name.lower():
                return "audio_effect"
            elif "midi_effect" in device.class_name.lower():
                return "midi_effect"
            else:
                return "unknown"
        except:
            return "unknown"

    def get_browser_tree(self, category_type="all"):
        """
        Get a simplified tree of browser categories.

        Args:
            category_type: Type of categories to get ('all', 'instruments', 'sounds', etc.)

        Returns:
            Dictionary with the browser tree structure
        """
        try:
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")

            # Check if browser is available
            if not hasattr(app, 'browser') or app.browser is None:
                raise RuntimeError("Browser is not available in the Live application")

            # Log available browser attributes to help diagnose issues
            browser_attrs = [attr for attr in dir(app.browser) if not attr.startswith('_')]
            self.log_message("Available browser attributes: {0}".format(browser_attrs))

            result = {
                "type": category_type,
                "categories": [],
                "available_categories": browser_attrs
            }

            # Helper function to process a browser item and its children
            def process_item(item, depth=0):
                if not item:
                    return None

                result = {
                    "name": item.name if hasattr(item, 'name') else "Unknown",
                    "is_folder": hasattr(item, 'children') and bool(item.children),
                    "is_device": hasattr(item, 'is_device') and item.is_device,
                    "is_loadable": hasattr(item, 'is_loadable') and item.is_loadable,
                    "uri": item.uri if hasattr(item, 'uri') else None,
                    "children": []
                }


                return result

            # Process based on category type and available attributes
            if (category_type == "all" or category_type == "instruments") and hasattr(app.browser, 'instruments'):
                try:
                    instruments = process_item(app.browser.instruments)
                    if instruments:
                        instruments["name"] = "Instruments"  # Ensure consistent naming
                        result["categories"].append(instruments)
                except Exception as e:
                    self.log_message("Error processing instruments: {0}".format(str(e)))

            if (category_type == "all" or category_type == "sounds") and hasattr(app.browser, 'sounds'):
                try:
                    sounds = process_item(app.browser.sounds)
                    if sounds:
                        sounds["name"] = "Sounds"  # Ensure consistent naming
                        result["categories"].append(sounds)
                except Exception as e:
                    self.log_message("Error processing sounds: {0}".format(str(e)))

            if (category_type == "all" or category_type == "drums") and hasattr(app.browser, 'drums'):
                try:
                    drums = process_item(app.browser.drums)
                    if drums:
                        drums["name"] = "Drums"  # Ensure consistent naming
                        result["categories"].append(drums)
                except Exception as e:
                    self.log_message("Error processing drums: {0}".format(str(e)))

            if (category_type == "all" or category_type == "audio_effects") and hasattr(app.browser, 'audio_effects'):
                try:
                    audio_effects = process_item(app.browser.audio_effects)
                    if audio_effects:
                        audio_effects["name"] = "Audio Effects"  # Ensure consistent naming
                        result["categories"].append(audio_effects)
                except Exception as e:
                    self.log_message("Error processing audio_effects: {0}".format(str(e)))

            if (category_type == "all" or category_type == "midi_effects") and hasattr(app.browser, 'midi_effects'):
                try:
                    midi_effects = process_item(app.browser.midi_effects)
                    if midi_effects:
                        midi_effects["name"] = "MIDI Effects"
                        result["categories"].append(midi_effects)
                except Exception as e:
                    self.log_message("Error processing midi_effects: {0}".format(str(e)))

            # Try to process other potentially available categories
            for attr in browser_attrs:
                if attr not in ['instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects'] and \
                   (category_type == "all" or category_type == attr):
                    try:
                        item = getattr(app.browser, attr)
                        if hasattr(item, 'children') or hasattr(item, 'name'):
                            category = process_item(item)
                            if category:
                                category["name"] = attr.capitalize()
                                result["categories"].append(category)
                    except Exception as e:
                        self.log_message("Error processing {0}: {1}".format(attr, str(e)))

            self.log_message("Browser tree generated for {0} with {1} root categories".format(
                category_type, len(result['categories'])))
            return result

        except Exception as e:
            self.log_message("Error getting browser tree: {0}".format(str(e)))
            self.log_message(traceback.format_exc())
            raise

    def get_browser_items_at_path(self, path):
        """
        Get browser items at a specific path.

        Args:
            path: Path in the format "category/folder/subfolder"
                 where category is one of: instruments, sounds, drums, audio_effects, midi_effects
                 or any other available browser category

        Returns:
            Dictionary with items at the specified path
        """
        try:
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")

            # Check if browser is available
            if not hasattr(app, 'browser') or app.browser is None:
                raise RuntimeError("Browser is not available in the Live application")

            # Log available browser attributes to help diagnose issues
            browser_attrs = [attr for attr in dir(app.browser) if not attr.startswith('_')]
            self.log_message("Available browser attributes: {0}".format(browser_attrs))

            # Parse the path
            path_parts = path.split("/")
            if not path_parts:
                raise ValueError("Invalid path")

            # Determine the root category
            root_category = path_parts[0].lower()
            current_item = None

            # Check standard categories first
            if root_category == "instruments" and hasattr(app.browser, 'instruments'):
                current_item = app.browser.instruments
            elif root_category == "sounds" and hasattr(app.browser, 'sounds'):
                current_item = app.browser.sounds
            elif root_category == "drums" and hasattr(app.browser, 'drums'):
                current_item = app.browser.drums
            elif root_category == "audio_effects" and hasattr(app.browser, 'audio_effects'):
                current_item = app.browser.audio_effects
            elif root_category == "midi_effects" and hasattr(app.browser, 'midi_effects'):
                current_item = app.browser.midi_effects
            else:
                # Try to find the category in other browser attributes
                found = False
                for attr in browser_attrs:
                    if attr.lower() == root_category:
                        try:
                            current_item = getattr(app.browser, attr)
                            found = True
                            break
                        except Exception as e:
                            self.log_message("Error accessing browser attribute {0}: {1}".format(attr, str(e)))

                if not found:
                    # If we still haven't found the category, return available categories
                    return {
                        "path": path,
                        "error": "Unknown or unavailable category: {0}".format(root_category),
                        "available_categories": browser_attrs,
                        "items": []
                    }

            # Navigate through the path
            for i in range(1, len(path_parts)):
                part = path_parts[i]
                if not part:  # Skip empty parts
                    continue

                if not hasattr(current_item, 'children'):
                    return {
                        "path": path,
                        "error": "Item at '{0}' has no children".format('/'.join(path_parts[:i])),
                        "items": []
                    }

                found = False
                for child in current_item.children:
                    if hasattr(child, 'name') and child.name.lower() == part.lower():
                        current_item = child
                        found = True
                        break

                if not found:
                    return {
                        "path": path,
                        "error": "Path part '{0}' not found".format(part),
                        "items": []
                    }

            # Get items at the current path
            items = []
            if hasattr(current_item, 'children'):
                for child in current_item.children:
                    item_info = {
                        "name": child.name if hasattr(child, 'name') else "Unknown",
                        "is_folder": hasattr(child, 'children') and bool(child.children),
                        "is_device": hasattr(child, 'is_device') and child.is_device,
                        "is_loadable": hasattr(child, 'is_loadable') and child.is_loadable,
                        "uri": child.uri if hasattr(child, 'uri') else None
                    }
                    items.append(item_info)

            result = {
                "path": path,
                "name": current_item.name if hasattr(current_item, 'name') else "Unknown",
                "uri": current_item.uri if hasattr(current_item, 'uri') else None,
                "is_folder": hasattr(current_item, 'children') and bool(current_item.children),
                "is_device": hasattr(current_item, 'is_device') and current_item.is_device,
                "is_loadable": hasattr(current_item, 'is_loadable') and current_item.is_loadable,
                "items": items
            }

            self.log_message("Retrieved {0} items at path: {1}".format(len(items), path))
            return result

        except Exception as e:
            self.log_message("Error getting browser items at path: {0}".format(str(e)))
            self.log_message(traceback.format_exc())
            raise
