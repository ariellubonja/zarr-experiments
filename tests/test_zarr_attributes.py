import unittest
import zarr
import yaml

from src.utils import write_utils
from src.dataset import NCAR_Dataset


# TODO Use parameterize to generate tests for each item in the queue
class VerifyNCARZarrAttributes(unittest.TestCase):
    def __init__(self):
        super().__init__()
        with open('config.yaml', 'r') as file:
            self.config = yaml.safe_load(file)

        # Initialize NCAR_Dataset High Rate
        self.ncar_datasets = [
            NCAR_Dataset(
                name='sabl2048b',
                location_path=self.config['NCAR_high_rate_paths'][0],
                desired_zarr_chunk_size=self.config['desired_zarr_chunk_length'],
                desired_zarr_array_length=self.config['desired_zarr_chunk_length'],
                prod_or_backup=self.config['prod_or_backup'],
                start_timestep=0,
                end_timestep=49
            ),
            NCAR_Dataset(
                name='sabl2048b',
                location_path=self.config['NCAR_high_rate_paths'][1],  # Split across 2 dirs
                desired_zarr_chunk_size=self.config['desired_zarr_chunk_length'],
                desired_zarr_array_length=self.config['desired_zarr_chunk_length'],
                prod_or_backup=self.config['prod_or_backup'],
                start_timestep=50,
                end_timestep=99
            ),
            NCAR_Dataset(
                name='sabl2048a',
                location_path=self.config['NCAR_low_rate_path'],
                desired_zarr_chunk_size=self.config['desired_zarr_chunk_length'],
                desired_zarr_array_length=self.config['desired_zarr_chunk_length'],
                prod_or_backup=self.config['prod_or_backup'],
                start_timestep=0,
                end_timestep=19
            )
        ]

    def test_all_timesteps(self):
        for dataset in self.ncar_datasets:
            for timestep in range(dataset.start_timestep, dataset.end_timestep + 1):
                lazy_zarr_cubes = dataset.transform_to_zarr(timestep)
                destination_paths = write_utils.get_zarr_array_destinations(dataset)

                for original_512, zarr_512_path in zip(lazy_zarr_cubes, destination_paths):
                    with self.subTest(timestep=timestep):
                        self.run_tests_for_single_file(zarr_512_path)

    def run_tests_for_single_file(self, zarr_512_path):
        zarr_512 = zarr.open_group(zarr_512_path, mode='r')
        self.verify_zarr_array_dims(zarr_512, zarr_512_path)
        self.verify_zarr_chunk_sizes(zarr_512, zarr_512_path)
        self.verify_zarr_compression(zarr_512, zarr_512_path)

    # TODO get Zarr group size from config.yaml
    def verify_zarr_array_dims(self, zarr_512, zarr_512_path):
        """
        Verify that the cube dimensions are as expected. Should be (512, 512, 512, 3) for velocity, (512, 512, 512, 1) otherwise
        """
        for var in zarr_512.array_keys():
            expected_shape = (512, 512, 512, 3) if var == "velocity" else (512, 512, 512, 1)
            self.assertEqual(zarr_512[var].shape, expected_shape)

        print("Cube dimension = (512, 512, 512, x),  for all variables in ", zarr_512_path)

    def verify_zarr_chunk_sizes(self, zarr_512, zarr_512_path):
        for var in zarr_512.array_keys():
            expected_chunksize = (64, 64, 64, 3) if var == "velocity" else (64, 64, 64, 1)
            self.assertEqual(zarr_512[var].chunks, expected_chunksize)

        print("Chunk sizes = (64, 64, 64, x),  for all variables in ", zarr_512_path)

    def verify_zarr_compression(self, zarr_512, zarr_512_path):
        for var in zarr_512.array_keys():
            self.assertIsNone(zarr_512[var].compressor)  # TODO get from config.yaml

        print("Compression is None for all variables in ", zarr_512_path)
