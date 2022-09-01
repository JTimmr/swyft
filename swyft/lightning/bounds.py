from typing import (
    Callable,
    Dict,
    Hashable,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from dataclasses import dataclass
import numpy as np
import torch
import swyft


@dataclass
class MeanStd:
    """Store mean and standard deviation"""
    mean: torch.Tensor
    std: torch.Tensor

    def from_samples(samples, weights = None):
        """
        Estimate mean and std deviation of samples by averaging over first dimension.
        Supports weights>=0 with weights.shape = samples.shape
        """
        if weights is None:
            weights = torch.ones_like(samples)
        mean = (samples*weights).sum(axis=0)/weights.sum(axis=0)
        res = samples - mean
        var = (res*res*weights).sum(axis=0)/weights.sum(axis=0)
        return MeanStd(mean = mean, std = var**0.5)

@dataclass
class RectangleBounds:
    params: torch.Tensor
    parnames: np.array

#def get_1d_rect_bounds(samples, th = 1e-6):
#    bounds = {}
#    r = samples.logratios
#    r = r - r.max(axis=0).values  # subtract peak
#    p = samples.params
#    all_max = p.max(dim=0).values
#    all_min = p.min(dim=0).values
#    constr_min = torch.where(r > np.log(th), p, all_max).min(dim=0).values
#    constr_max = torch.where(r > np.log(th), p, all_min).max(dim=0).values
#    #bound = torch.stack([constr_min, constr_max], dim = -1)
#    bound = RectangleBound(constr_min, constr_max)
#    return bound

def rect_bounds_from_tensors(params: torch.Tensor, logratios: torch.Tensor, threshold = 1e-6):
    """Takes parameter array and logratios array and extracts rectangular bounds
    
    Args:
        params: (Nsamples, *Nparams, Ndim)
        logratios: (Nsamples, *Nparams)
    
    Returns:
        np.Tensor: (*Nparams, Ndim, 2)
    """
    # Generate mask (all points with maximum likelihood ratio above threshold are kept)
    mask = logratios - logratios.max(axis=0).values > np.log(threshold)
    par_max = params.max(dim=0).values
    par_min = params.min(dim=0).values
    constr_min = torch.where(mask.unsqueeze(-1), params, par_max).min(dim=0).values
    constr_max = torch.where(mask.unsqueeze(-1), params, par_min).max(dim=0).values
    return torch.stack([constr_min, constr_max], dim=-1)


def get_rect_bounds(logratios, threshold = 1e-6):
    logratios = swyft.lightning.core._collection_mask(logratios, lambda x: isinstance(x, swyft.lightning.core.LogRatioSamples))
    def map_fn(logratios):
        bounds = rect_bounds_from_tensors(logratios.params, logratios.logratios, threshold = threshold)
        return RectangleBounds(params = bounds, parnames = logratios.parnames)
    return swyft.lightning.core._collection_map(logratios, lambda x: map_fn(x))
