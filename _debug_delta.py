import pandas as pd
import sys
import numpy as np
sys.path.insert(0, r'C:\ThermalBench\Temperature-Test-Automation')
from ui.graph_preview.graph_plot_helpers import extract_unit_from_column, get_measurement_type_label, load_run_csv_dataframe

fpath = r'C:\Users\intel 11\AppData\Local\ThermalBench\runs\checking for png generation\CPUGPU_W0_L0_V11\run_window.csv'
df_data, cols = load_run_csv_dataframe(fpath)
df_raw = df_data[cols]

print('=== Columns ===')
for i, c in enumerate(df_raw.columns):
    unit = extract_unit_from_column(c)
    label = get_measurement_type_label(unit)
    print(f'  [{i}] {repr(c)} -> unit={repr(unit)} -> {label}')

# Simulate _find_ambient_col
def find_ambient(df):
    if 'Ambient [°C]' in df.columns:
        return 'Ambient [°C]'
    candidates = [c for c in df.columns if 'ambient' in str(c).lower()]
    return candidates[0] if candidates else None

amb_col = find_ambient(df_raw)
print(f'\nAmbient col: {repr(amb_col)}')
if amb_col:
    print(f'Ambient data sample: {df_raw[amb_col].head(3).tolist()}')
    ser = pd.to_numeric(df_raw[amb_col], errors='coerce')
    print(f'Ambient any-valid: {bool(ser.notna().any())}')

# Simulate _temperature_column_indices
temp_idxs = [i for i, c in enumerate(df_raw.columns)
             if get_measurement_type_label(extract_unit_from_column(str(c))).strip().lower() == 'temperature']
print(f'\ntemp_idxs: {temp_idxs}')

# Check ambient index
if amb_col:
    amb_idxs = [i for i, c in enumerate(df_raw.columns) if str(c) == str(amb_col)]
    print(f'amb_idxs: {amb_idxs}')
    if amb_idxs:
        amb_idx = amb_idxs[0]
        temp_idxs2 = [i for i in temp_idxs if i != amb_idx]
        print(f'temp_idxs2 (after excluding ambient): {temp_idxs2}')
        
        # Simulate the subtraction
        amb_arr = pd.to_numeric(df_raw.iloc[:, amb_idx], errors='coerce').to_numpy(dtype=float)
        y_mat = df_raw.iloc[:, temp_idxs2].to_numpy(dtype=float)
        delta_mat = y_mat - amb_arr.reshape((-1, 1))
        print(f'\nOriginal first row temp values: {y_mat[0]}')
        print(f'Ambient first row: {amb_arr[0]}')
        print(f'Delta first row: {delta_mat[0]}')
        print(f'\nSubtraction WORKS: {not np.allclose(y_mat, delta_mat)}')
