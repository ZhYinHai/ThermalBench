import pandas as pd
import numpy as np

from ui.graph_preview.graph_preview import GraphPreview


def main() -> int:
    csv_path = r"c:\TempTesting\runs\626 - Top Panel temperature test\CPUGPU_W1_L2_V4\run_window.csv"
    df = pd.read_csv(csv_path)

    idx = pd.to_datetime(
        df.iloc[:, 0].astype(str) + " " + df.iloc[:, 1].astype(str),
        dayfirst=True,
        errors="coerce",
    )
    df_raw = df.iloc[:, 2:].copy()
    df_raw.index = idx
    df_raw = df_raw.loc[~df_raw.index.isna()]

    gp = GraphPreview.__new__(GraphPreview)
    gp._temp_delta_mode = True
    gp._preview_csv_path = csv_path
    gp._preview_df_all_raw = df_raw

    print('raw columns contain ambient?', 'Ambient [°C]' in df_raw.columns)
    print('raw head cpu/amb', df_raw[['CPU Package [°C] #1','Ambient [°C]']].head(3).to_dict('records'))

    df_disp = GraphPreview._build_display_df(gp)

    print('df_disp type', type(df_disp))
    if isinstance(df_disp, pd.DataFrame):
        print('disp head cpu/amb', df_disp[['CPU Package [°C] #1','Ambient [°C]']].head(3).to_dict('records'))

    col_cpu = "CPU Package [°C] #1"
    col_amb = "Ambient [°C]"

    a = pd.to_numeric(df_raw[col_amb], errors="coerce").to_numpy(dtype=float)
    y0 = pd.to_numeric(df_raw[col_cpu], errors="coerce").to_numpy(dtype=float)
    y1 = pd.to_numeric(df_disp[col_cpu], errors="coerce").to_numpy(dtype=float)

    mask = np.isfinite(a) & np.isfinite(y0) & np.isfinite(y1)
    i = int(np.flatnonzero(mask)[0])

    print("raw", float(y0[i]), "amb", float(a[i]), "disp", float(y1[i]), "exp", float(y0[i] - a[i]))
    print("maxdiff", float(np.nanmax(np.abs((y0 - a) - y1))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
