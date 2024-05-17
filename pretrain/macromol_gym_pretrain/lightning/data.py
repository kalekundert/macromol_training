import lightning as L
import macromol_voxelize as mmvox
import logging
import os

from ..torch.data import (
        CnnNeighborDataset, NeighborParams, ImageParams, InfiniteSampler,
)
from ..geometry import cube_faces
from torch.utils.data import DataLoader

from typing import Optional

log = logging.getLogger('macromol_gym_pretrain')

class CnnNeighborDataModule(L.LightningDataModule):

    def __init__(
            self,
            db_path,
            *,

            # Neighbor parameters
            neighbor_padding_A: float,
            noise_max_distance_A: float,
            noise_max_angle_deg: float,

            # Image parameters
            grid_length_voxels: int,
            grid_resolution_A: float,
            atom_radius_A: Optional[float] = None,
            element_channels: list[str],
            ligand_channel: bool,

            # Data loader parameters
            batch_size: int,
            train_epoch_size: Optional[int] = None,
            val_epoch_size: Optional[int] = None,
            test_epoch_size: Optional[int] = None,
            num_workers: Optional[int] = None,
    ):
        super().__init__()
        self.save_hyperparameters()

        if atom_radius_A is None:
            atom_radius_A = grid_resolution_A / 2

        grid = mmvox.Grid(
                length_voxels=grid_length_voxels,
                resolution_A=grid_resolution_A,
        )
        datasets = {
                split: CnnNeighborDataset(
                    db_path=db_path,
                    split=split,
                    neighbor_params=NeighborParams(
                        direction_candidates=cube_faces(),
                        distance_A=grid.length_A + neighbor_padding_A,
                        noise_max_distance_A=noise_max_distance_A,
                        noise_max_angle_deg=noise_max_angle_deg,
                    ),
                    img_params=ImageParams(
                        grid=grid,
                        atom_radius_A=atom_radius_A,
                        element_channels=element_channels,
                        ligand_channel=ligand_channel,
                    ),
                )
                for split in ['train', 'val', 'test']
        }
        num_workers = get_num_workers(num_workers)

        def make_dataloader(split, sampler=None):
            log.info("making dataloader: split=%s num_workers=%d", split, num_workers)

            return DataLoader(
                    dataset=datasets[split],
                    sampler=sampler,
                    batch_size=batch_size,
                    num_workers=num_workers,
                    
                    # For some reason I don't understand, my worker processes
                    # get killed by SIGABRT if I use the 'fork' context.  The
                    # behavior is very sensitive to all sorts of small changes
                    # in the code (e.g. `debug()` calls), which makes me think
                    # it's some sort of race condition.
                    multiprocessing_context='spawn' if num_workers else None,
                    persistent_workers=bool(num_workers),

                    pin_memory=True,
                    drop_last=True,
            )

        self._train_dataloader = make_dataloader(
                split='train',
                sampler=InfiniteSampler(
                    train_epoch_size or len(datasets['train']),
                ),
        )
        self._val_dataloader = make_dataloader(
                split='val',
                sampler=InfiniteSampler(
                    val_epoch_size or len(datasets['val']),
                    increment_across_epochs=False,
                ),
        )
        self._test_dataloader = make_dataloader(
                split='test',
                sampler=InfiniteSampler(
                    test_epoch_size or len(datasets['test']),
                    increment_across_epochs=False,
                ),
        )

    def train_dataloader(self):
        return self._train_dataloader

    def val_dataloader(self):
        return self._val_dataloader

    def test_dataloader(self):
        return self._test_dataloader

def get_num_workers(num_workers: Optional[int]) -> int:
    if num_workers is not None:
        return num_workers

    try:
        return int(os.environ['SLURM_JOB_CPUS_PER_NODE'])
    except KeyError:
        return os.cpu_count()


