"""
Simulate toggle_delta_mode in isolation to find the real bug.
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r'C:\ThermalBench\Temperature-Test-Automation')

from ui.graph_preview.graph_plot_helpers import (
    extract_unit_from_column, get_measurement_type_label, load_run_csv_dataframe
)

fpath = r'C:\Users\intel 11\AppData\Local\ThermalBench\runs\checking for png generation\CPUGPU_W0_L0_V11\run_window.csv'
df_data, cols = load_run_csv_dataframe(fpath)
df_raw = df_data[cols]

print(f"df_raw columns: {list(df_raw.columns)}")
print(f"df_raw shape: {df_raw.shape}")
print()

# Simulate the full _build_display_df logic with _temp_delta_mode=True
def find_ambient_col(df):
    if 'Ambient [°C]' in df.columns:
        return 'Ambient [°C]'
    candidates = [c for c in df.columns if 'ambient' in str(c).lower()]
    return candidates[0] if candidates else None

def temperature_column_indices(df):
    out = []
    for i, col in enumerate(list(df.columns)):
        try:
            unit = extract_unit_from_column(str(col))
            if str(get_measurement_type_label(unit)).strip().lower() == 'temperature':
                out.append(int(i))
        except Exception:
            continue
    return out

# Step 1: Find ambient
amb_col = find_ambient_col(df_raw)
print(f"1. amb_col = {repr(amb_col)}")

# Check all-NaN
if amb_col:
    ser = pd.to_numeric(df_raw[amb_col], errors='coerce')
    print(f"   Ambient notna any: {bool(ser.notna().any())}")
    if not bool(ser.notna().any()):
        amb_col = None  # Fall through to sidecar
        print("   -> Set to None (all-NaN), would try sidecar")

if not amb_col:
    print("   -> No ambient found, _build_display_df would return df_raw unchanged!")
    sys.exit(1)

# Step 2: Find ambient index
amb_idxs = [i for i, c in enumerate(list(df_raw.columns)) if str(c) == str(amb_col)]
print(f"2. amb_idxs = {amb_idxs}")
if not amb_idxs:
    print("   -> No amb_idxs, would return df_raw!")
    sys.exit(1)
amb_idx = amb_idxs[0]

# Step 3: Get ambient series
amb = pd.to_numeric(df_raw.iloc[:, amb_idx], errors='coerce')
print(f"3. Ambient series mean: {amb.mean():.2f}, std: {amb.std():.4f}")

# Step 4: Find temperature indices
temp_idxs = temperature_column_indices(df_raw)
print(f"4. temp_idxs = {temp_idxs}")
if not temp_idxs:
    print("   -> No temp_idxs, would return df_raw!")
    sys.exit(1)

# Step 5: Exclude ambient from temp indices
temp_idxs2 = [int(i) for i in list(temp_idxs) if int(i) != int(amb_idx)]
print(f"5. temp_idxs2 = {temp_idxs2}")
if not temp_idxs2:
    print("   -> temp_idxs2 empty, would return df_disp unchanged (copy of raw)!")
    sys.exit(1)

# Step 6: Build the delta df
df_disp = df_raw.copy(deep=True)
try:
    amb_arr = pd.to_numeric(df_raw.iloc[:, amb_idx], errors='coerce').to_numpy(dtype=float)
except Exception as e:
    print(f"Error getting amb_arr: {e}")
    sys.exit(1)

try:
    y_mat = df_raw.iloc[:, temp_idxs2].to_numpy(dtype=float, copy=False)
except Exception as e:
    print(f"Error getting y_mat: {e}")
    try:
        cols_temp = [df_raw.columns[int(i)] for i in temp_idxs2]
        tmp = df_raw.loc[:, cols_temp].apply(lambda s: pd.to_numeric(s, errors='coerce'))
        y_mat = np.asarray(tmp.to_numpy(), dtype=float)
        print(f"   -> Used fallback y_mat")
    except Exception as e2:
        print(f"   -> Fallback also failed: {e2}")
        sys.exit(1)

try:
    delta_mat = y_mat - amb_arr.reshape((-1, 1))
except Exception as e:
    print(f"Error computing delta_mat: {e}")
    try:
        delta_mat = y_mat - amb_arr[:, None]
        print("   -> Used fallback subtraction")
    except Exception as e2:
        print(f"   -> Fallback also failed: {e2}")
        sys.exit(1)

# Step 7: Assign back
delta_mat = np.asarray(delta_mat, dtype=float)
print(f"6. delta_mat first row: {delta_mat[0]}")
print(f"   y_mat first row: {y_mat[0]}")
print(f"   Values changed: {not np.allclose(y_mat, delta_mat)}")

try:
    cols_orig = df_disp.columns
    df_disp.columns = range(int(df_disp.shape[1]))
    df_disp.loc[:, temp_idxs2] = delta_mat
    df_disp.columns = cols_orig
    print("   Assignment method 1: SUCCESS")
except Exception as e:
    print(f"   Assignment method 1 FAILED: {e}")
    try:
        cols_orig = df_disp.columns
        df_disp.columns = range(int(df_disp.shape[1]))
        for j, col_i in enumerate(temp_idxs2):
            df_disp.loc[:, int(col_i)] = np.asarray(delta_mat[:, int(j)], dtype=float)
        df_disp.columns = cols_orig
        print("   Assignment method 2 (per-column): SUCCESS")
    except Exception as e2:
        print(f"   Assignment method 2 FAILED: {e2}")

# Verify df_disp has the right values
print(f"\n7. Verification:")
for c in list(df_raw.columns)[:3]:
    raw_val = df_raw[c].iloc[0]
    disp_val = df_disp[c].iloc[0]
    print(f"   {c}: raw={raw_val}, disp={disp_val}, changed={raw_val != disp_val}")

# Check if df_disp IS df_raw (same object)
print(f"\n8. df_disp is df_raw: {df_disp is df_raw}")
print(f"   df_disp data matches df_raw: {df_disp.equals(df_raw)}")
