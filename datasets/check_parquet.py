import sys

import pyarrow.parquet as pq

def count_rows(parquet_file):
    table = pq.read_table(parquet_file)
    return table.num_rows

if __name__ == "__main__":
    # parquet_file = "/home/fsq/hf_home/hub/datasets--ritzzai--GUI-R1/snapshots/ca55ddaa180c5e8f8b27003221c391efa10a1f52/train.parquet"  # 3570
    parquet_file = "/home/fsq/hf_home/hub/datasets--ritzzai--GUI-R1/snapshots/ca55ddaa180c5e8f8b27003221c391efa10a1f52/test.parquet"     # 1000
    rows = count_rows(parquet_file)
    print(f"Number of rows: {rows}")