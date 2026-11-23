# MakerWorld Settings Importer for OrcaSlicer

An OrcaSlicer 2.5+ Python plugin that converts MakerWorld `.3mf` project files (typically sliced for Bambu Lab printers) to your **currently active printer** without losing the creator's print settings—and without overwriting your printer hardware, build plate, or filament profiles.

## Why Use This?
Normally, when you open a MakerWorld `.3mf` in OrcaSlicer and switch the printer to your own machine, OrcaSlicer resets the creator's process settings (walls, infill, layer height, supports, etc.) or locks the saved preset to the original Bambu printer.

This plugin solves that by taking your active **Printer + Bed + Filament + Process** configuration as a clean baseline, overlaying only the compatible **Process** settings from the MakerWorld `.3mf`, and saving a ready-to-slice `{filename}_{printer}.3mf` alongside a detailed `.txt` change log.

## Features
- **Preserves Your Setup:** Keeps your active Printer, Nozzle Variant, Build Plate, Filament(s), and base Process preset intact.
- **Process-Only Overlay:** Copies Quality, Strength, Speed, Support, and Other print settings while ignoring incompatible Bambu-specific keys.
- **Printer-Named Output:** Automatically names output files using your active printer (e.g., `model_Qidi_Q2_0.4_nozzle.3mf` and `model_Qidi_Q2_0.4_nozzle_report.txt`).
- **Built-in Safety Checks:** Flags nozzle diameter mismatches, multi-color/AMS slot differences, unsaved printer changes, and speeds/accelerations that exceed your printer's hardware limits.
- **Categorized Change Log:** Writes every genuinely modified setting (`old --> new`) grouped by OrcaSlicer tab—filtering out unchanged scalar/list noise—plus a full list of skipped unsupported settings.
- **Cross-Platform:** Uses native OS file pickers on Windows (PowerShell with UTF-8 support), macOS (`osascript`), and Linux (`zenity` or `kdialog`).

## Installation
1. Download `makerworld_importer.py` from this repository.
2. Open **OrcaSlicer 2.5.0+**.
3. Go to **Plugins** -> **Browse plugins** -> **Install local plugin** and select `makerworld_importer.py`.
4. Check the **Activate** box next to **MakerWorld Settings Importer**.

## How to Use
1. Start a clean project (`Ctrl + N`) and select your desired **Printer**, **Filament**, and base **Process** preset.
2. Open **Plugins** and run **Convert MakerWorld 3MF (Process Only)**.
3. Select the downloaded MakerWorld `.3mf` file.
4. Open the newly generated `{filename}_{printer}.3mf` file in OrcaSlicer and review `{filename}_{printer}_report.txt` for any warnings or skipped settings before slicing.

## Permissions Setup (Required Before Running)

OrcaSlicer 2.5.0+ runs plugins inside a strict security sandbox. Before using the plugin, open **Plugins -> MakerWorld Settings Importer -> Permissions** and choose either **Option A (1-Click)** or **Option B (Strict Granular Setup)**.

### Option A: 1-Click Setup (Easiest)
- Toggle **Full access** to **ON** at the top of the **Permissions** tab.
- *(Note: This plugin is 100% offline, single-file open-source Python and never uses network access.)*

---

### Option B: Strict Granular Setup (Without Full Access)
If you prefer to keep **Full access** turned **OFF**, configure the sections in the **Permissions** tab exactly as shown below:

#### 1. Files and folders
Add the folder where you store/download your `.3mf` files (ending with `\*` on Windows or `/*` on macOS/Linux) to **BOTH** **Read** and **Read and write**:
- **Read:**
  `C:\Users\YourName\Desktop\3DPrinter\Projects\*` *(or `C:\Users\YourName\Downloads\*`)*
- **Read and write:**
  `C:\Users\YourName\Desktop\3DPrinter\Projects\*` *(or `C:\Users\YourName\Downloads\*`)*

*(Why: Grants permission to read the original MakerWorld `.3mf` and write the converted `.3mf` and `_report.txt` in that directory.)*

#### 2. Network access
- Leave completely **empty / disabled** (this plugin works 100% offline).

#### 3. Execution -> Processes
Add the file-picker process command(s) for your operating system:
- **Windows (add both entries):**
  1. `powershell`
  2. `powershell -NoProfile -STA -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.OpenFileDialog; $f.Title = 'Select MakerWorld 3MF File'; $f.Filter = '3MF Files (*.3mf)|*.3mf'; if ($f.ShowDialog() -eq 'OK') { Write-Output $f.FileName }"`
- **macOS:**
  - `osascript`
- **Linux:**
  - `zenity` *(or `kdialog`)*

*(Why: Allows the plugin to open your operating system's native file selection window.)*

#### 4. Execution -> Threading
- Toggle **Allow this plugin to create threads** to **ON**.

*(Why: Python's `subprocess.run()` internally spawns reader threads to capture the file path returned by the OS file picker.)*

#### 5. Audit events -> Events
Add both of these file-operation events (click **Add** after each one):
- `FsRead:open`
- `FsWrite:open`

*(Why: Required by Python's `zipfile` module, report writer, and subprocess pipe handles when opening and saving files.)*

## Notes
- **Linux Users:** Requires `zenity` (GNOME/GTK) or `kdialog` (KDE/Qt) installed for the file picker dialog.
- **Manual Install:** If installing outside Orca Cloud, download `makerworld_importer_any.py` and load it via **Plugins -> Browse plugins -> Install local plugin**.

## Changelog
- **v1.0.2:**
  - **Cleaner Report Diffs:** Aligns Bambu vs. OrcaSlicer scalar and list value formats (`1` vs. `['1']`) to eliminate false-positive diffs in `_report.txt` and preserve expected JSON types across single and multi-toolhead printers.
- **v1.0.1:**
  - **Layer Height & Preset-Default Retention:** Fixed `layer_height`, `initial_layer_print_height`, and other preset-default keys resetting to your base preset on load by registering all changed process keys in `different_settings_to_system[0]` and preserving active filament/printer diff slots *(thanks to @orca_5c685f038f for reporting!)*.
  - **Safer `.3mf` Repackaging:** Strips pre-sliced source G-code (`plate_*.gcode`, `.gcode.md5`) and `slice_info.config` caches for hardware safety—preventing foreign printer start macros, temperatures, or bed limits from accidentally running on your machine and forcing a clean re-slice—and adds case-insensitive `Metadata/project_settings.config` lookup.
- **v1.0.0:** Initial release

## License
MIT
