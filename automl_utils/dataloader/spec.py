# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tooling used to create reference implementations of dataloaders from published works."""

import abc
import dataclasses as dc
import enum
from typing import Any, Callable, Mapping, Optional, Tuple

import torch.utils.data as tud


class Phase(enum.Enum):
    """An enum signaling whether the dataset will be used for architecture search or genotype evaluation.

    Valid options include SEARCH and EVAL.
    """

    SEARCH: str = "search"
    EVAL: str = "eval"


class Split(enum.Enum):
    """An enum signaling whether the dataset represents training or validation data.

    Valid options include TRAIN and VAL.
    """

    TRAIN: str = "train"
    VAL: str = "val"


@dc.dataclass
class BatchConfig:
    """How individual samples should be transformed and aggregated into a batch.

    Attributes:
        batch_size: int
            How many samples should be aggregated into a single batch.
        input_transform: Optional[Callable]
            How the inputs should be transformed. None corresponds to no transformation.
        target_transform: Optional[Callable]
            How the targets should be transformed. None corresponds to no transformation.
    """

    batch_size: int
    input_transform: Optional[Callable]
    target_transform: Optional[Callable]

    def __post_init__(self) -> None:
        """Validates that the batch_size is strictly positive."""
        if self.batch_size <= 0:
            raise ValueError(f"`batch_size` must be > 0. Received value of {self.batch_size}.")


class DataloaderSpec(abc.ABC):
    """The base class from which all reference implementation data strategies should be derived."""

    def __init__(
        self,
        search_split: Optional[float] = None,
        config_map: Optional[Mapping[Tuple[Phase, Split], BatchConfig]] = None,
    ):
        """Creates a new `DataloaderSpec`.

        Parameters
        ----------
        search_split: Optional[float], optional
            Overrides the reference implementation split of the training dataset for train/val in the search phase.
            Defaults to None, which signals the split from the reference implementation should be used.
        config_map: Optional[Mapping[Tuple[Phase, Split], BatchConfig]], optional
            Overrides the reference implementation batch sizes and input/target transforms for dataloaders used for
            training and validation data in the search and genotype evaluation phases. Any tuple not specified will
            default to those of the reference implementation. Defaults to None (no override of any dataloader config).
        """
        if search_split is None:
            search_split = self.get_default_search_split()
        elif search_split < 0 or search_split > 1:
            raise ValueError(f"`search_split` must be between 0 and 1 inclusive, received {search_split}")
        self._search_split = search_split

        self._config_map = {}
        config_map = config_map or {}
        for p in Phase:
            for s in Split:
                if (p, s) in config_map:
                    self._config_map[(p, s)] = config_map[(p, s)]
                else:
                    self._config_map[(p, s)] = self.get_default_config(p, s)

    @abc.abstractmethod
    def load_dataset(
        self,
        phase: Phase,
        split: Split,
        path: str,
        input_transform: Optional[Callable],
        target_transform: Optional[Callable],
        search_split: Optional[float] = None,
        download: bool = False,
    ) -> Optional[tud.Dataset]:
        """A method, to be overridden in derived classes, that produces the requested dataset.

        Parameters
        ----------
        phase: Phase
            The phase in which the dataloader is to be used.
        split: Split
            The split of the data being requested.
        path: str
            The path where the data may be found (or should be stored if `download`=True and it does not yet exist).
        input_transform: Optional[Callable]
            How the input data should be transformed. If None, no transform should be applied.
        target_transform: Optional[Callable]
            How the target data should be transformed. If None, no transform should be applied.
        search_split: Optional[float], optional
            How the training set should be split in the search phase for the train/val dataloaders. Defaults to None,
            but must be in the range [0, 1] inclusive if `phase` == Phase.SEARCH.
        download: bool, optional
            Whether the data should be downloaded if it does not yet exist at the specified `path`. Defaults to False.

        Returns
        -------
        Optional[torch.utils.data.Dataset]
            The corresponding Dataset (or None if the requested split would result in a null Dataloader)
        """
        raise NotImplementedError

    @staticmethod
    @abc.abstractmethod
    def get_default_config(phase: Phase, split: Split) -> BatchConfig:
        """Returns the `BatchConfig` used to reproduce the dataloader from the reference implementation."""
        raise NotImplementedError

    @staticmethod
    @abc.abstractmethod
    def get_default_search_split() -> float:
        """Returns the split of the training dataset used during search in the reference implementation."""
        raise NotImplementedError

    def get_config(self, phase: Phase, split: Split) -> BatchConfig:
        """Returns the `BatchConfig` to be used, including any overrides, in dataloader creation."""
        return self._config_map[(phase, split)]

    @property
    def search_split(self) -> float:
        """Returns the training dataset split used during search, including any overrides, in dataloader creation."""
        return self._search_split

    def get_dataloader(
        self, phase: Phase, split: Split, path: str, download: bool = False, **kwargs: Any
    ) -> Optional[tud.DataLoader]:
        """Returns the dataloader that follows the reference implementation except for user-supplied overrides.

        Parameters
        ----------
        phase: Phase
            The phase in which the dataloader is to be used.
        split: Split
            The split of the data being requested.
        path: str
            The path where the data may be found (or should be stored if `download`=True and it does not yet exist).
        download: bool, optional
            Whether the dataset should be downloaded if it does not yet exist at the supplied path. Defaults to False.
        kwargs
            Any additional kwargs to be passed to the dataloader upon creation.

        Returns
        -------
        Optional[tud.DataLoader]
            The requested dataloader (or None if the requested configuration would results in an empty dataloader).
        """
        config = self.get_config(phase, split)
        ds_args = {"search_split": self.search_split} if phase == phase.SEARCH else {}
        dset = self.load_dataset(
            phase, split, path, config.input_transform, config.target_transform, download=download, **ds_args
        )
        if not dset:
            return None

        if "batch_size" in kwargs and kwargs["batch_size"] != config.batch_size:
            raise ValueError(
                f'`batch_size` specified in kwargs ({kwargs["batch_size"]}) does not match '
                f"`BatchConfig.batch_size` ({config.batch_size})."
            )

        kwargs = {"batch_size": config.batch_size, **(kwargs or {})}
        return tud.DataLoader(dset, **kwargs)