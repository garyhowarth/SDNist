from typing import List, Union, Optional
import numpy as np
import pandas as pd
import json
import os
import time
import sys

from pathlib import Path


def create_bins(bins_range: dict):
    bins = dict()
    for f, br in bins_range.items():
        if 'bin_type' not in br:
            fbm = br['first_bin_max']
            lbm = br['last_bin_min']
            bs = br['bin_size']
            bins[f] = np.r_[-np.inf, np.arange(fbm, lbm, bs), np.inf]
        elif 'bin_type' in br and br['bin_type'] == 'time':
            fbm = br['first_bin_max_hour']
            lbm = br['last_bin_min_hour']
            bs = br['bin_size_minutes']
            bins[f] = np.r_[-np.inf, [h * 100 + m for h in range(fbm, lbm) for m in range(0, 60, bs)],
                            np.inf]
    return bins


def discretize(dataset: pd.DataFrame, schema: dict, bins_range: dict, copy: bool = True):
    """ Discretizes `dataset` using `pandas.CategoricalDtypes`. All values are remapped
    from 0 to `n-1` where `n` is the number of distinct values.

    :param dataset pandas.DataFrame: dataset to discretize
    :param schema dict: dataset schema, as provided by `sdnist.census` for instance.
    :param bins_range dict: a dict containing
        (`feature`, `Dict('first_bin_max', 'last_bin_max', 'bin_size', 'type')) key-value pairs`.
        The bins used to compute the k-marginal score are available at
        `sdnist.kmarginal.CensusKMarginalScore.BINS` and
        `sdnist.kmarginal.TaxiKMarginalScore.BINS`.
    :param copy bool: whether the original `dataset` should be copied before discretization.
    :return: the discretized input `pandas.DataFrame`.

    """
    bins = create_bins(bins_range)
    if copy:
        dataset = dataset.copy()
    for column in dataset:
        if column in bins:
            if column in schema and "has_null" in schema[column]:
                desc = schema[column]
                null_val = desc['null_value']
                dataset[column] = pd.to_numeric(dataset[column].replace(null_val, desc["min"] - 1))
            dataset[column] = pd.cut(dataset[column], bins[column], right=False).cat.codes

        elif column in schema:
            desc = schema[column]
            if "values" in desc:
                dataset[column] = dataset[column].astype(pd.CategoricalDtype(desc["values"])).cat.codes
            elif "min" in desc.keys():
                if "has_null" in desc:
                    null_val = desc['null_value']
                    dataset[column] = dataset[column].replace(null_val, desc["min"]-1)
                dataset[column] = pd.to_numeric(dataset[column])
                dataset[column] = (dataset[column] - desc["min"]).astype(int)
            else:
                #feature unmodified, e.g., 'kind == "ID"' columns
                pass
        else:
            # Feature is not modified.
            pass
    return dataset


def undo_discretize(dataset, schema, bins, copy: bool = True, handle_inf: bool = True):
    """ Return an unbinned dataset. """
    if handle_inf:
        # In some cases, the bin interval includes infinity
        # In that case, undoing the discretization is slightly harder
        original_bins = bins
        bins = {}

        for col, bin in original_bins.items():
            bins[col] = bin.copy()
            bins[col][-1] = bin[-2] + 1

    if copy:
        dataset = dataset.copy()

    for column in dataset:
        if column in bins:
            bin = bins[column]
            dataset[column] = bin[dataset[column].values]

        elif column in schema:
            desc = schema[column]
            if "values" in desc:
                dataset[column] = np.array(desc["values"])[dataset[column].values]
            elif "min" in desc:
                dataset[column] += desc["min"]
            else:
                raise ValueError("Unknown column, probably due to invalid schema")
        
        else:
            # Column is not known to bins or schema 
            # -> do not do anything
            pass

    return dataset


def unstack(dataset, user_id: str = "sim_individual_id", time: str = "YEAR", flat: bool = False):
    df = dataset.set_index([user_id, time]).unstack(time, fill_value=-1)

    if flat:
        df.columns = df.columns.to_flat_index()

    return df


def stack(dataset, user_id: str = "sim_individual_id", time: str = "YEAR"):
    if not isinstance(dataset.columns, pd.MultiIndex):
        dataset.columns = pd.MultiIndex.from_tuples(dataset.columns)

    df = dataset.stack().reset_index()

    # Rename year and user_id
    col_names = list(df)
    df.rename(columns={
        col_names[0]: user_id,
        col_names[1]: time
    }, inplace=True)

    # Remove empty rows
    keep = (df != -1).all(axis="columns")
    return df[keep]


def read_json(path: Path):
    with open(path, 'r') as f:
        return json.load(f)


def save_data_frame(data: pd.DataFrame, output_dir: Path, filename: str) -> Path:
    p = Path(output_dir, f'{filename}.csv')
    data.to_csv(p)
    return p


def relative_path(path: Union[List[Path], Path]) -> Union[List[str], str]:
    if isinstance(path, Path):
        return "/".join(list(path.parts)[-2:])
    elif isinstance(path, List):
        return ["/".join(list(p.parts)[-2:])
                for p in path]


def create_path(path: Path):
    if not path.exists():
        os.mkdir(path)


def to_num(data: pd.DataFrame) -> pd.DataFrame:
    """Converts data to numeric and drops all the records
    with N values"""
    d = data

    for f in d.columns:
        d = d[~d[f].isin(['N'])]
    for f in d.columns:
        d.loc[:, f] = pd.to_numeric(d.loc[:, f])
    return d


def df_filter(data: pd.DataFrame, filters: Optional[List] = None) -> pd.DataFrame:
    """
    Filters dataframe using input filters.
    Filters are represented as a List, each member
    of the filters list is a list that contains feature name
    at index 0 and list of feature values at index 1.
    """
    if not filters:
        return data
    for d_filter in filters:
        feature = d_filter[0]
        values = d_filter[1]
        data = data[data[feature].isin(values)]
    return data


class SimpleLogger:
    ptrn = '/*\*/'  # separator pattern

    def __init__(self):
        self.level_messages = dict()
        self.current_head = None
        self.current_level = None
        self.msg_path = dict()
        self.root = None

    def msg(self, message: str, level=1, timed=True):
        if timed:
            if self.root is None:
                self.root = message
                self.current_level = level

            t = Time()
            t.start(message)
            msg_full_path = self.get_msg_path(message, level)
            self.current_level = level
            self.current_head = msg_full_path
            self.level_messages[self.current_head] = (message, level, t)

        if level < 3:
            level_indent = '|' + ''.join(['--'
                                          for _ in range(level)])

            sys_print(level_indent + ' ' + message)
        elif not timed:
            level_indent = '|' + ''.join(['--'
                                          for _ in range(level)])

            sys_print(level_indent + ' ' + message)

    def end_msg(self):
        head_data = self.level_messages[self.current_head]
        message, level, t = head_data
        del self.level_messages[self.current_head]
        if self.current_head != self.root:
            parent_path = self.ptrn.join(self.current_head.split(self.ptrn)[:-1])
            self.current_head = parent_path
            self.current_level = level - 1
        secs = t.time()
        level_indent = '|' + ''.join(['--'
                                      for _ in range(level)])
        sys_print(level_indent + f' >>>> Finished {message} | Time: {round(secs, 1)}s <<<<')

    def get_msg_path(self, msg, level):
        if self.current_level == level and self.current_head != self.root:
            if self.current_head:
                parent_path = self.ptrn.join(self.current_head.split(self.ptrn)[:-1])
                msg_path = parent_path + self.ptrn + msg
                return msg_path
            else:
                self.current_head = self.root
                return self.current_head
        else:
            return self.current_head + self.ptrn + msg


class Time:
    def __init__(self):
        self.labels = dict()
        self.last_label = None

    def start(self, label: str):
        self.last_label = label
        self.labels[label] = time.time()

    def time(self):
        if not self.last_label:
            sys_print('sdnist.utils.Time.time() Invalid Use of Time: No Label Found')
            return
        start = self.labels[self.last_label]
        end = time.time() - start
        self.labels[self.last_label] = end
        return self.labels[self.last_label]


def sys_print(data: str):
    sys.stdout.flush()
    sys.stdout.write(data + '\n')
