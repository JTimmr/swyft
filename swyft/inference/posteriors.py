import logging
from typing import Callable, Dict, Optional, Sequence, Tuple, Union
from warnings import warn

import numpy as np
import torch

import swyft
from swyft.inference.ratios import RatioEstimator
from swyft.inference.train import TrainOptions
from swyft.networks import DefaultHead, DefaultTail
from swyft.saveable import StateDictSaveable
from swyft.types import (
    Array,
    Device,
    MarginalIndex,
    MarginalToArray,
    ObsType,
    ParameterNamesType,
    PathType,
)
from swyft.utils import tupleize_marginals

log = logging.getLogger(__name__)


class Posteriors(StateDictSaveable):
    """Main inference class.

    Args:
        dataset: Dataset for which we want to perform inference.

    .. note::
        The dataset will be used to extract `parameter_names`, the
        prior and its bound. It will be then set as default dataset for
        training.
    """

    def __init__(self, dataset: "swyft.Dataset") -> None:
        self._parameter_names = dataset.parameter_names
        self._prior_truncator = swyft.PriorTruncator(dataset.prior, bound=dataset.bound)
        self._ratios = {}
        self._dataset = dataset

    @property
    def parameter_names(self) -> ParameterNamesType:
        """Parameter names. Inherited from dataset."""
        return self._parameter_names

    def add(
        self,
        marginals: MarginalIndex,
        head: Callable[..., "swyft.Module"] = DefaultHead,
        tail: Callable[..., "swyft.Module"] = DefaultTail,
        head_args: dict = {},
        tail_args: dict = {},
        device: Device = "cpu",
    ) -> None:
        """Add marginals.

        Args:
            marginals
            head (swyft.Module instance or type): Head network (optional).
            tail (swyft.Module instance or type): Tail network (optional).
            head_args (dict): Keyword arguments for head network instantiation.
            tail_args (dict): Keyword arguments for tail network instantiation.
        """
        marginals = tupleize_marginals(marginals)
        re = RatioEstimator(
            marginals,
            device=device,
            head=head,
            tail=tail,
            head_args=head_args,
            tail_args=tail_args,
        )
        self._ratios[marginals] = re

    def to(
        self, device: Device, marginals: Optional[MarginalIndex] = None
    ) -> "Posteriors":
        """Move networks to device.

        Args:
            device: Targeted device.
            marginals: Optional, only move networks related to specific marginals.
        """
        if marginals is not None:
            marginals = tupleize_marginals(marginals)
            self._ratios[marginals].to(device)
        else:
            for _, v in self._ratios.items():
                v.to(device)
        return self

    @property
    def dataset(self) -> "swyft.Dataset":
        """Default training dataset."""
        return self._dataset

    def set_dataset(self, dataset: "swyft.Dataset") -> None:
        """Set default training dataset."""
        self._dataset = dataset

    def train(
        self,
        marginals: MarginalIndex,
        batch_size: int = 64,
        validation_size: float = 0.1,
        early_stopping_patience: int = 5,
        max_epochs: int = 30,
        optimizer: Callable[..., torch.optim.Optimizer] = torch.optim.Adam,
        optimizer_args: dict = dict(lr=1e-3),
        scheduler: Callable[
            ..., torch.optim.lr_scheduler._LRScheduler
        ] = torch.optim.lr_scheduler.ReduceLROnPlateau,
        scheduler_args: dict = dict(factor=0.1, patience=5),
        nworkers: int = 2,
        non_blocking: bool = True,
    ) -> None:
        """Train marginals.

        Args:
            batch_size (int): Batch size...
            TODO
        """
        if self._dataset is None:
            print("ERROR: No dataset specified.")
            return
        if self._dataset.requires_sim:
            print("ERROR: Not all points in the dataset are simulated yet.")
            return

        marginals = tupleize_marginals(marginals)
        re = self._ratios[marginals]

        trainoptions = TrainOptions(
            batch_size=batch_size,
            validation_size=validation_size,
            early_stopping_patience=early_stopping_patience,
            max_epochs=max_epochs,
            optimizer=optimizer,
            optimizer_args=optimizer_args,
            scheduler=scheduler,
            scheduler_args=scheduler_args,
            nworkers=nworkers,
            non_blocking=non_blocking,
        )

        re.train(self._dataset, trainoptions)

    def train_diagnostics(self, marginals: MarginalIndex):
        marginals = tupleize_marginals(marginals)
        return self._ratios[marginals].train_diagnostics()

    def eval(
        self, v: Array, obs0: ObsType, n_batch: int = 100
    ) -> Dict[str, Tuple[np.ndarray, MarginalToArray, ParameterNamesType]]:
        """Returns weighted posterior.

        Args:
            v: Parameter array
            obs0: Observation of interest
            n_batch: number of samples to produce in each batch
        """
        # Unmasked original wrongly normalized log_prob densities
        # log_probs = self._prior_truncator.log_prob(v)
        u = self._prior_truncator.prior.cdf(v)

        ratios = self._eval_ratios(
            obs0, u, n_batch=n_batch
        )  # evaluate lnL for reference observation
        weights = {}
        for k, val in ratios.items():
            weights[k] = np.exp(val)
        return dict(v=v, weights=weights, parameter_names=self.parameter_names)

    def sample(
        self, N: int, obs0: ObsType, n_batch: int = 100
    ) -> Dict[str, Tuple[np.ndarray, MarginalToArray, ParameterNamesType]]:
        """Returns weighted posterior samples for given observation.

        Args:
            N: Number of samples to return
            obs0: Observation of interest
            n_batch: number of samples to produce in each batch
        """
        v = self._prior_truncator.sample(N)  # prior samples
        return self.eval(v, obs0, n_batch=n_batch)

    #    # TODO: Still needs to be fixed?
    #    def _rejection_sample(
    #        self,
    #        N: int,
    #        obs0: ObsType,
    #        excess_factor: int = 10,
    #        maxiter: int = 1000,
    #        n_batch: int = 10_000,
    #        PoI: Sequence[MarginalIndex] = None,
    #    ):
    #        """Samples from each marginal using rejection sampling.
    #
    #        Args:
    #            N: number of samples in each marginal to output
    #            obs0: target observation
    #            excess_factor: N_to_reject = excess_factor * N
    #            maxiter: maximum loop attempts to draw N
    #            n_batch: how many proposed samples are drawn at once
    #            PoI: selection of parameters of interest
    #
    #        Returns:
    #            Marginal posterior samples. keys are marginal tuples, values are samples/
    #
    #        Reference:
    #            Section 23.3.3
    #            Machine Learning: A Probabilistic Perspective
    #            Kevin P. Murphy
    #        """
    #
    #        weighted_samples = self.sample(N=excess_factor * N, obs0=obs0, n_batch=10_000)
    #
    #        maximum_log_likelihood_estimates = {
    #            k: np.log(np.max(v)) for k, v in weighted_samples["weights"].items()
    #        }
    #
    #        PoI = set(weighted_samples["weights"].keys()) if PoI is None else PoI
    #        collector = {k: [] for k in PoI}
    #        out = {}
    #
    #        # Do the rejection sampling.
    #        # When a particular key hits the necessary samples, stop calculating on it to reduce cost.
    #        # Send that key to out.
    #        counter = 0
    #        remaining_param_tuples = PoI
    #        while counter < maxiter:
    #            # Calculate chance to keep a sample
    #
    #            log_prob_to_keep = {
    #                pt: np.log(weighted_samples["weights"][pt])
    #                - maximum_log_likelihood_estimates[pt]
    #                for pt in remaining_param_tuples
    #            }
    #
    #            # Draw and determine if samples are kept
    #            to_keep = {
    #                pt: np.less_equal(np.log(np.random.rand(*v.shape)), v)
    #                for pt, v in log_prob_to_keep.items()
    #            }
    #
    #            # Collect samples for every tuple of parameters, if there are enough, add them to out.
    #            for param_tuple in remaining_param_tuples:
    #                kept_all_params = weighted_samples["v"][to_keep[param_tuple]]
    #                kept_params = kept_all_params[..., param_tuple]
    #                collector[param_tuple].append(kept_params)
    #                concatenated = np.concatenate(collector[param_tuple])[:N]
    #                if len(concatenated) == N:
    #                    out[param_tuple] = concatenated
    #
    #            # Remove the param_tuples which we already have in out, thus not to calculate for them anymore.
    #            for param_tuple in out.keys():
    #                if param_tuple in remaining_param_tuples:
    #                    remaining_param_tuples.remove(param_tuple)
    #                    log.debug(f"{len(remaining_param_tuples)} param tuples remaining")
    #
    #            if len(remaining_param_tuples) > 0:
    #                weighted_samples = self.sample(
    #                    N=excess_factor * N, obs0=obs0, n_batch=n_batch
    #                )
    #            else:
    #                return out
    #            counter += 1
    #        warn(
    #            f"Max iterations {maxiter} reached there were not enough samples produced in {remaining_param_tuples}."
    #        )
    #        return out

    @property
    def bound(self) -> "swyft.bounds.Bound":
        return self._prior_truncator.bound

    @property
    def prior(self) -> "swyft.bounds.Prior":
        return self._prior_truncator.prior

    def truncate(self, marginals: MarginalIndex, obs0: ObsType) -> "swyft.bounds.Bound":
        """Generate and return new bound object."""
        marginals = tupleize_marginals(marginals)
        bound = swyft.Bound.from_Posteriors(marginals, self, obs0)
        print("Bounds: Truncating...")
        print("Bounds: ...done. New volue is V=%.4g" % bound.volume)
        return bound

    def _eval_ratios(
        self, obs: ObsType, v: Array, n_batch: int = 100
    ) -> MarginalToArray:
        result = {}
        for _, rc in self._ratios.items():
            ratios = rc.ratios(obs, v, n_batch=n_batch)
            result.update(ratios)
        return result

    def empirical_mass(
        self, nobs: int = 1000, npost: int = 1000
    ) -> Dict[Tuple[int, ...], Dict[str, Array]]:
        """Estimate empirical vs nominal mass.

        Args:
            nobs: Number of mock observations for empirical mass estimate (taken randomly from dataset)
            npost: Number of posterior samples to estimate nominal mass

        Returns:
            Nominal and empirical masses.
        """
        raise NotImplementedError()
        # return estimate_empirical_mass(self.dataset, self, nobs, npost)

    def state_dict(self) -> dict:
        state_dict = dict(
            prior_truncator=self._prior_truncator.state_dict(),
            ratios={k: v.state_dict() for k, v in self._ratios.items()},
            parameter_names=self._parameter_names,
        )
        return state_dict

    @classmethod
    def from_state_dict(cls, state_dict: dict, dataset: "swyft.Dataset" = None):
        obj = Posteriors.__new__(Posteriors)
        obj._prior_truncator = swyft.PriorTruncator.from_state_dict(
            state_dict["prior_truncator"]
        )
        obj._ratios = {
            k: RatioEstimator.from_state_dict(v)
            for k, v in state_dict["ratios"].items()
        }
        obj._parameter_names = state_dict["parameter_names"]
        obj._dataset = dataset
        return obj

    def save(self, filename: PathType) -> None:
        """
        .. note::
            The dataset is not saved, but can be specified during `load` if necessary.
        """
        sd = self.state_dict()
        torch.save(sd, filename)

    @classmethod
    def load(cls, filename: PathType, dataset: "swyft.Dataset" = None):
        """Load posterior.

        Args:
            filename
            dataset

        .. warning::
            Make sure that the dataset is the same that was originally used for training the posterior.
        """
        sd = torch.load(filename)
        return cls.from_state_dict(sd, dataset=dataset)
