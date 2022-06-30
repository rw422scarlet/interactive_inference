import os
import re
import numpy as np
import pandas as pd

import torch
from torch.utils.data import DataLoader, random_split

def load_data(data_path, scenario, filename):
    """ Load processed data 
    
    Args:
        data_path (str): root data path. e.g. ../interaction-dataset-master
        scenario (str): scenario name. e.g. DR_CHN_Merging_ZS
        filename (str): track file name. e.g. vehicle_tracks_007.csv
    
    Returns:
        df_track (pd.dataframe): concatenated processed track data
    """
    df_track = pd.read_csv(
        os.path.join(data_path, "recorded_trackfiles", scenario, filename)
    )
    df_kf = pd.read_csv(
        os.path.join(data_path, "processed_trackfiles", "kalman_filter", scenario, filename)
    ).drop(columns=["track_id", "frame_id"])
    df_neighbors = pd.read_csv(
        os.path.join(data_path, "processed_trackfiles", "neighbors", scenario, filename)
    ).drop(columns=["track_id", "frame_id"])
    df_features = pd.read_csv(
        os.path.join(data_path, "processed_trackfiles", "features", scenario, filename)
    ).drop(columns=["track_id", "frame_id"])
    df_labels = pd.read_csv(
        os.path.join(data_path, "processed_trackfiles", "train_labels", scenario, filename)
    ).drop(columns=["track_id", "frame_id"])
    df_track = pd.concat([df_track, df_kf, df_neighbors, df_features, df_labels], axis=1)
    df_track["psi_rad"] = np.clip(df_track["psi_rad"], -np.pi, np.pi)

    # add scenario and record id
    record_id = re.compile(r"\d\d\d").search(filename).group()
    df_track.insert(0, "scenario", scenario)
    df_track.insert(0, "record_id", record_id)
    return df_track

def train_test_split(dataset, train_ratio, batch_size, collate_fn=None, seed=0):
    gen = torch.Generator()
    gen.manual_seed(seed)
    
    train_size = np.ceil(train_ratio * len(dataset)).astype(int)
    test_size = len(dataset) - train_size
    
    train_set, test_set = random_split(
        dataset, [train_size, test_size], generator=gen
    )
    
    train_loader = DataLoader(
        train_set, batch_size, shuffle=True, collate_fn=collate_fn, drop_last=True
    )
    test_loader = DataLoader(
        test_set, batch_size, shuffle=False, collate_fn=collate_fn, drop_last=False
    )
    return train_loader, test_loader

def count_parameters(model):
    """ Count active parameters in model """
    num_params = 0
    for n, p in model.named_parameters():
        if p.requires_grad == True:
            num_params += np.prod(list(p.shape))
    return num_params