# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "MakerWorld Settings Importer"
# description = "Safely imports MakerWorld 3MF process settings onto your active printer with nozzle, speed, and multi-color checks."
# author = "FleeceFlames"
# version = "1.0.1"
# ///
# SPDX-License-Identifier: MIT

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
import orca  # type: ignore  # Embedded module provided by OrcaSlicer at runtime

# =============================================================================
# EXCLUSION FILTERS
# =============================================================================
# Keys starting with these prefixes belong to physical printer hardware,
# G-code macros, or build plate (bed) configuration. We ignore them from the
# incoming MakerWorld 3MF so your active printer's baseline stays untouched.
PRINTER_AND_BED_PREFIXES = (
    "machine_",
    "printer_",
    "printable_",
    "bed_",
    "curr_bed_type",
    "extruder_",
    "nozzle_",
    "retraction_",
    "deretraction_",
    "z_hop",
    "gcode_",
    "host_",
    "print_host",
    "max_",
    "min_",
    "thumbnails",
    "auxiliary_fan",
    "change_filament_gcode",
    "before_layer_change_gcode",
    "layer_change_gcode",
    "time_lapse_gcode",
    "scan_first_layer",
    "use_relative_e_distances",
    "use_firmware_retraction",
    "upward_compatible_machine",
    "best_object_pos",
    "default_print_profile",
    "default_filament_profile",
)

# Keys starting with these prefixes belong to filament profiles, temperatures,
# cooling fans, and pressure advance. Ignoring them keeps your active filament
# profile intact.
FILAMENT_PREFIXES = (
    "filament_",
    "nozzle_temperature",
    "hot_plate_temp",
    "cool_plate_temp",
    "eng_plate_temp",
    "textured_plate_temp",
    "supertack_plate_temp",
    "chamber_temperature",
    "fan_",
    "overhang_fan_",
    "close_fan_",
    "full_fan_",
    "additional_cooling_fan",
    "slow_down_",
    "reduce_fan_",
    "pressure_advance",
    "enable_pressure_advance",
    "activate_air_filtration",
    "during_print_exhaust_fan",
    "complete_print_exhaust_fan",
    "temperature_vitrification",
    "required_nozzle_HRC",
    "idle_temperature",
    "ooze_prevention",
    "compatible_prints",
    "inherits",
)

# Exact metadata/profile keys that should never be copied from the source 3MF.
EXACT_EXCLUDED_KEYS = {
    "wipe",
    "from",
    "version",
    "name",
    "print_settings_id",
    # v1.0.1 fix (reported by Halil Çimen): never copy source diff list verbatim
    "different_settings_to_system",
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def is_excluded_key(key: str) -> bool:
    """Returns True if a config key should be preserved from the active printer instead of copied."""
    return (
        key in EXACT_EXCLUDED_KEYS
        or key.startswith(PRINTER_AND_BED_PREFIXES)
        or key.startswith(FILAMENT_PREFIXES)
    )


def is_stale_slice_cache(filename: str) -> bool:
    """Identifies cached source slice files that should be stripped from converted 3MFs."""
    low = filename.lstrip("/").lower()
    return low == "metadata/slice_info.config" or (
        low.startswith("metadata/plate_")
        and (low.endswith(".gcode") or low.endswith(".gcode.md5"))
    )


def clean_label(name: str) -> str:
    """Strips OrcaSlicer's leading '* ' unsaved-changes indicator from preset names."""
    if not name:
        return ""
    return name[2:] if name.startswith("* ") else name


def sanitize_filename_part(name: str) -> str:
    """
    Converts a printer name into a safe filename suffix across Windows, macOS, and Linux.
    Example: 'Qidi Q2 0.4 nozzle - Copy' -> 'Qidi_Q2_0.4_nozzle_-_Copy'
    """
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    safe = re.sub(r"\s+", "_", safe.strip())
    safe = re.sub(r"_+", "_", safe).strip("._")
    return safe or "converted"


def normalize_orca_value(val):
    """
    Converts Python types returned by bundle.full_config_value() into the
    string/list format expected inside OrcaSlicer's project_settings.config JSON.
    """
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        return [normalize_orca_value(v) for v in val]
    return val


def to_float(val):
    """Safely extracts a float from a string, number, percentage string, or single-item list."""
    if isinstance(val, list) and val:
        val = val[0]
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def categorize_setting(key: str) -> str:
    """Groups OrcaSlicer process keys into UI tab names for a readable TXT report."""
    k = key.lower()
    if any(t in k for t in ("speed", "accel", "jerk", "travel")):
        return "Speed"
    if any(t in k for t in ("support", "raft", "tree_support")):
        return "Support"
    if any(t in k for t in ("wall", "infill", "shell", "sparse", "solid", "top_shell", "bottom_shell")):
        return "Strength"
    if any(t in k for t in ("layer", "line_width", "seam", "ironing", "bridge", "overhang", "elefant", "xy_", "scarf", "arachne")):
        return "Quality"
    return "Other"


def pick_3mf_file():
    """
    Opens a native OS file picker without requiring tkinter (not bundled in OrcaSlicer).
    Returns a tuple: (selected_filepath, error_message_or_None).
    """
    if sys.platform == "win32":
        ps_script = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.OpenFileDialog; "
            "$f.Title = 'Select MakerWorld 3MF File'; "
            "$f.Filter = '3MF Files (*.3mf)|*.3mf'; "
            "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.FileName }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.strip(), None

    elif sys.platform == "darwin":
        script = 'POSIX path of (choose file of type {"3mf"} with prompt "Select MakerWorld 3MF File")'
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip(), None

    else:
        if shutil.which("zenity"):
            cmd = [
                "zenity",
                "--file-selection",
                "--title=Select MakerWorld 3MF File",
                "--file-filter=3MF files (*.3mf) | *.3mf",
            ]
        elif shutil.which("kdialog"):
            cmd = [
                "kdialog",
                "--getopenfilename",
                os.path.expanduser("~"),
                "*.3mf | 3MF Files",
            ]
        else:
            return "", "No file picker found. Please install 'zenity' or 'kdialog' to use this plugin on Linux."

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        return result.stdout.strip(), None


# =============================================================================
# ORCASLICER PLUGIN CAPABILITY & REGISTRATION
# =============================================================================
class MakerWorldImporterScript(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Convert MakerWorld 3MF (Process Only)"

    def execute(self):
        # 1. Read active Printer, Filament, and Process presets from OrcaSlicer
        bundle = orca.host.preset_bundle()
        printer = bundle.current_printer_preset()
        if not printer:
            return orca.ExecutionResult.error(
                "No active printer detected. Select your printer first."
            )

        raw_printer_label = printer.label()
        printer_name = clean_label(raw_printer_label)
        filament_names = [clean_label(f) for f in bundle.current_filament_preset_names()]
        process = bundle.current_process_preset()
        process_name = clean_label(process.label()) if process else ""
        valid_keys = set(bundle.full_config_keys())

        warnings = []

        # Guardrail 1: Warn if the active printer preset has unsaved modifications ('* ')
        if raw_printer_label.startswith("* "):
            warnings.append(
                f"Unsaved printer changes detected ('{raw_printer_label}'). "
                "For best results, start from a clean New Project (Ctrl+N)."
            )

        # 2. Snapshot the active Printer + Bed + Filament + Process baseline
        converted_config = {}
        for k in valid_keys:
            try:
                val = bundle.full_config_value(k)
                if val is not None:
                    converted_config[k] = normalize_orca_value(val)
            except Exception:
                pass

        # 3. Prompt the user to choose a MakerWorld .3mf file
        input_path, picker_err = pick_3mf_file()
        if picker_err:
            return orca.ExecutionResult.error(picker_err)
        if not input_path:
            return orca.ExecutionResult.success("Conversion canceled.")

        # 4. Extract Metadata/project_settings.config from the .3mf ZIP archive
        try:
            with zipfile.ZipFile(input_path, "r") as zin:
                target_entry = next(
                    (n for n in zin.namelist() if n.lstrip("/").lower() == "metadata/project_settings.config"),
                    None,
                )
                if not target_entry:
                    return orca.ExecutionResult.error(
                        "No 'Metadata/project_settings.config' found in this 3MF."
                    )
                raw_config = zin.read(target_entry)
                mw_settings = json.loads(raw_config.decode("utf-8"))
        except Exception as e:
            return orca.ExecutionResult.error(f"Failed to read 3MF: {e}")

        # Guardrail 2: Warn if the MakerWorld file was sliced for a different nozzle size
        mw_nozzle = to_float(mw_settings.get("nozzle_diameter"))
        my_nozzle = to_float(converted_config.get("nozzle_diameter"))
        if mw_nozzle and my_nozzle and abs(mw_nozzle - my_nozzle) > 0.01:
            warnings.append(
                f"Nozzle mismatch: MakerWorld file uses a {mw_nozzle}mm nozzle, "
                f"but your active printer is {my_nozzle}mm. Review layer height and line widths."
            )

        # Guardrail 3: Warn if the MakerWorld file uses more AMS/filament slots than loaded
        mw_filaments = mw_settings.get("filament_settings_id")
        if isinstance(mw_filaments, list) and len(mw_filaments) > max(1, len(filament_names)):
            warnings.append(
                f"Multi-color project: MakerWorld file uses {len(mw_filaments)} filament slots, "
                f"but your active setup has {len(filament_names)}. Check object filament assignments."
            )

        # Guardrail 4: Read active printer speed/acceleration hardware limits
        max_speed_x = to_float(converted_config.get("machine_max_speed_x"))
        max_accel_x = to_float(converted_config.get("machine_max_acceleration_x"))

        # 5. Overlay ONLY supported Process settings onto the active baseline
        categorized_changes = {
            "Quality": [],
            "Strength": [],
            "Speed": [],
            "Support": [],
            "Other": [],
        }
        changed_process_keys = set()
        unchanged_count = 0
        ignored_printer_or_filament = 0
        skipped_unsupported = []

        for key, value in mw_settings.items():
            if is_excluded_key(key):
                ignored_printer_or_filament += 1
            elif key not in valid_keys:
                skipped_unsupported.append(f"{key} = {value}")
            else:
                old_val = converted_config.get(key)
                new_val = normalize_orca_value(value)

                # Flag speeds/accelerations that exceed the active printer's limits
                num_val = to_float(new_val)
                if num_val is not None:
                    if max_speed_x and key.endswith("_speed") and not str(new_val).endswith("%"):
                        if num_val > max_speed_x:
                            warnings.append(
                                f"Speed exceeds printer limit: {key} ({num_val} mm/s > max {max_speed_x} mm/s)."
                            )
                    elif max_accel_x and key.endswith("_acceleration") and not str(new_val).endswith("%"):
                        if num_val > max_accel_x:
                            warnings.append(
                                f"Acceleration exceeds printer limit: {key} ({num_val} mm/s^2 > max {max_accel_x} mm/s^2)."
                            )

                # Record only values that actually differ from the active preset
                if old_val != new_val:
                    changed_process_keys.add(key)
                    cat = categorize_setting(key)
                    categorized_changes[cat].append(f"{key}: {old_val}  -->  {new_val}")
                else:
                    unchanged_count += 1

                converted_config[key] = new_val

        for cat in categorized_changes:
            categorized_changes[cat].sort()
        skipped_unsupported.sort()
        total_changed = sum(len(v) for v in categorized_changes.values())

        # 6. Lock the output 3MF to your active Printer, Filament, and Process IDs
        converted_config["printer_settings_id"] = printer_name
        converted_config["compatible_printers_condition"] = ""
        if filament_names:
            converted_config["filament_settings_id"] = filament_names
        if process_name:
            converted_config["print_settings_id"] = process_name

        # v1.0.1 fix (reported by Halil Çimen):
        # Register EVERY changed process key in different_settings_to_system[0]
        # so OrcaSlicer preserves preset-default keys (like layer_height and
        # initial_layer_print_height) instead of resetting them to the user's
        # base process preset on load.
        expected_slots = max(1, len(filament_names)) + 2  # process + N filaments + printer
        diff_list = converted_config.get("different_settings_to_system")
        if not isinstance(diff_list, list):
            diff_list = [""] * expected_slots
        else:
            diff_list = [str(x) if x is not None else "" for x in diff_list]
            if len(diff_list) < expected_slots:
                diff_list.extend([""] * (expected_slots - len(diff_list)))

        existing_proc_diffs = {k.strip() for k in diff_list[0].split(";") if k.strip()}
        existing_proc_diffs.update(changed_process_keys)
        diff_list[0] = ";".join(sorted(existing_proc_diffs))
        converted_config["different_settings_to_system"] = diff_list

        # 7. Build output paths using the sanitized printer name
        base, ext = os.path.splitext(input_path)
        printer_suffix = sanitize_filename_part(printer_name)
        output_path = f"{base}_{printer_suffix}{ext}"
        report_path = f"{base}_{printer_suffix}_report.txt"

        # 8. Repackage the .3mf archive with the updated project_settings.config
        try:
            with zipfile.ZipFile(input_path, "r") as zin, zipfile.ZipFile(
                output_path, "w", zipfile.ZIP_DEFLATED
            ) as zout:
                for item in zin.infolist():
                    if is_stale_slice_cache(item.filename):
                        continue
                    if item.filename == target_entry:
                        zout.writestr(
                            item,
                            json.dumps(converted_config, indent=4).encode("utf-8"),
                        )
                    else:
                        zout.writestr(item, zin.read(item.filename))
        except Exception as e:
            return orca.ExecutionResult.error(f"Failed to write converted 3MF: {e}")

        # 9. Format and save the human-readable conversion report (.txt)
        lines = [
            "============================================================",
            "             MAKERWORLD 3MF CONVERSION REPORT               ",
            "============================================================",
            f"Source File : {os.path.basename(input_path)}",
            f"Saved 3MF   : {output_path}",
            "",
            "--- Locked Active Presets ---",
            f"Printer     : {printer_name}",
            f"Filament(s) : {', '.join(filament_names) if filament_names else 'None'}",
            f"Process     : {process_name or 'None'}",
            "",
            "--- Summary ---",
            f"Settings Changed           : {total_changed}",
            f"Settings Already Matching  : {unchanged_count}",
            f"Printer/Filament Preserved : {ignored_printer_or_filament}",
            f"Unsupported Skipped        : {len(skipped_unsupported)}",
            "",
        ]

        if warnings:
            lines.append("============================================================")
            lines.append(f"  WARNINGS ({len(warnings)}) - REVIEW BEFORE SLICING")
            lines.append("============================================================")
            for w in warnings:
                lines.append(f"  [!] {w}")
            lines.append("")

        lines.append("============================================================")
        lines.append(f"  CHANGED PROCESS SETTINGS ({total_changed})")
        lines.append("============================================================")
        if total_changed == 0:
            lines.append("  No process settings differed from your active preset.")
        else:
            for cat, items in categorized_changes.items():
                if items:
                    lines.append(f"\n  [{cat}] ({len(items)})")
                    for entry in items:
                        lines.append(f"    - {entry}")

        lines.append("")
        lines.append("============================================================")
        lines.append(f"  SKIPPED UNSUPPORTED SETTINGS ({len(skipped_unsupported)})")
        lines.append("============================================================")
        if skipped_unsupported:
            for entry in skipped_unsupported:
                lines.append(f"  - {entry}")
        else:
            lines.append("  All process settings were compatible with your printer!")

        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            return orca.ExecutionResult.error(
                f"3MF converted, but failed to save report TXT: {e}"
            )

        # 10. Return a concise status message to OrcaSlicer's UI
        warn_note = f" | {len(warnings)} WARNING(S) - check report!" if warnings else ""
        summary = (
            f"Done! Changed: {total_changed} | Skipped: {len(skipped_unsupported)}{warn_note}\n"
            f"3MF: {output_path}\n"
            f"Report: {report_path}"
        )
        return orca.ExecutionResult.success(summary)


@orca.plugin
class MakerWorldSmartImporter(orca.base):
    """Main plugin entry point discovered by OrcaSlicer."""
    def register_capabilities(self):
        orca.register_capability(MakerWorldImporterScript)
