import pytest
from functools import partial
from itertools import product
import tempfile

import torch
import torch.nn as nn
import numpy as np

from swyft.estimation import Points, RatioEstimator
from swyft.cache import MemoryCache
from swyft.intensity import get_unit_intensity


def sim_repeat_noise(theta, num_copies):
    noise = np.random.randn(num_copies, *theta.shape)
    expanded_theta = np.expand_dims(theta, axis=0)
    return expanded_theta + noise


def setup_points():
    zdim = 10
    num_copies = 3
    xshape = (num_copies, zdim)
    expected_n = 100
    simulator = partial(sim_repeat_noise, num_copies=num_copies)

    cache = MemoryCache(zdim, xshape)
    intensity = get_unit_intensity(expected_n, zdim)
    cache.grow(intensity)
    cache.simulate(simulator)
    return cache, Points(cache, intensity)


class Head(nn.Module):
    def __init__(self):
        super().__init__()
        self.flatten = torch.nn.Flatten()
    
    def forward(self, x):
        return self.flatten(x)


class TestPoints:
    def test_points_save_load(self):
        cache, points = setup_points()
        with tempfile.NamedTemporaryFile() as tf:
            points.save(tf.name)

            loaded = Points.load(cache, tf.name)

        gather_attrs = lambda x: (
            x.indices,
            x.xshape,
            x.zdim,
            x.intensity.expected_n,
            x.intensity.area,
            x.intensity.factor_mask.intervals,
        )
        assert [
            np.allclose(i, j)
            for i, j in zip(gather_attrs(loaded), gather_attrs(points))
        ]


class TestRatioEstimator:
    @pytest.mark.parametrize("head", (None, Head()))
    def test_ratio_estimator_save_load(self, head):
        cache, points = setup_points()
        re = RatioEstimator(points, head=head)
        with tempfile.NamedTemporaryFile() as tf:
            re.save(tf.name)

            loaded = RatioEstimator.load(cache, tf.name)

        gather_attrs = lambda x: (
            x.combinations,
            *(v for _, v in x.net_state_dict.items()),
            *(v for _, v in x.ratio_cache.items()),
            x.points.indices,
            x.points.xshape,
            x.points.zdim,
            x.points.intensity.expected_n,
            x.points.intensity.area,
            x.points.intensity.factor_mask.intervals,
        )
        assert [
            np.allclose(i, j) for i, j in zip(gather_attrs(loaded), gather_attrs(re))
        ]

        # TODO fix error with loading a flattened head.


if __name__ == "__main__":
    pass
