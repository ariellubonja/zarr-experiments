import unittest
import zarr
from dask.array.utils import assert_eq
import dask.array as da
from parameterized import parameterized
import yaml
import argparse

from src.utils.write_utils import get_sharding_queue
from src.dataset import NCAR_Dataset
from src.utils import write_utils


parser = argparse.ArgumentParser(description="Run Zarr data correctness tests")
parser.add_argument("--dataset", type=str, required=True, help="Dataset name",
                    choices=["NCAR-High-Rate-1", "NCAR-High-Rate-2", "NCAR-Low-Rate"])
parser.add_argument("--start_timestep", type=int, required=True, help="Start timestep for testing")
parser.add_argument("--end_timestep", type=int, required=True, help="End timestep for testing (inclusive)")
args = parser.parse_args()

# Store arguments in global variables
DATASET = args.dataset
START_TIMESTEP = args.start_timestep
END_TIMESTEP = args.end_timestep


# TODO tons of duplicated code with test_zarr_attributes.py
class VerifyZarrDataCorrectness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open('tests/config.yaml', 'r') as file:
            cls.config = yaml.safe_load(file)

    def setUp(self):
        # Set up individual test instance with datasets
        self.ncar_datasets = {
            "NCAR-High-Rate-1": NCAR_Dataset(
                name='sabl2048b',
                location_path=self.config['NCAR_high_rate_paths'][0],
                desired_zarr_chunk_size=self.config['desired_zarr_chunk_length'],
                desired_zarr_array_length=self.config['desired_zarr_chunk_length'],
                prod_or_backup=self.config['prod_or_backup'],
                start_timestep=0,
                end_timestep=49
            ),
            "NCAR-High-Rate-2": NCAR_Dataset(
                name='sabl2048b',
                location_path=self.config['NCAR_high_rate_paths'][1],
                desired_zarr_chunk_size=self.config['desired_zarr_chunk_length'],
                desired_zarr_array_length=self.config['desired_zarr_chunk_length'],
                prod_or_backup=self.config['prod_or_backup'],
                start_timestep=50,
                end_timestep=99
            ),
            "NCAR-Low-Rate": NCAR_Dataset(
                name='sabl2048a',
                location_path=self.config['NCAR_low_rate_path'][0],
                desired_zarr_chunk_size=self.config['desired_zarr_chunk_length'],
                desired_zarr_array_length=self.config['desired_zarr_chunk_length'],
                prod_or_backup=self.config['prod_or_backup'],
                start_timestep=0,
                end_timestep=19
            )
        }

        global DATASET, START_TIMESTEP, END_TIMESTEP
        self.dataset = self.ncar_datasets[DATASET]
        self.start_timestep = START_TIMESTEP
        self.end_timestep = END_TIMESTEP

    def test_all_timesteps(self):
        dataset = self.ncar_datasets[self.dataset]
        for timestep in range(self.start_timestep, self.end_timestep + 1):
            lazy_zarr_cubes = dataset.transform_to_zarr(timestep)  # Still Original data, before write
            # Where Zarr data was written
            destination_paths = write_utils.get_zarr_array_destinations(dataset, timestep)

            # TODO Parameterized?
            for original_data_cube, written_zarr_cube in zip(lazy_zarr_cubes, destination_paths):
                with self.subTest(timestep=timestep):
                    self.verify_zarr_group_data(original_data_cube, written_zarr_cube)

    @parameterized.expand(get_sharding_queue())
    def verify_zarr_group_data(self, original_subarray, zarr_group_path):
        '''
        Verify correctness of data contained in each zarr group against
        the original data file (NCAR NetCDF)

        Args:
            original_subarray (xarray.Dataset): (Sub)Array of original data that was written as zarr to zar_group_path
            zarr_group_path (str): Location of the sub-chunked data as a Zarr Group
        '''
        zarr_group = zarr.open_group(zarr_group_path, mode='r')
        print("Comparing original 512^3 with ", zarr_group_path)
        for var in original_subarray.data_vars:
            assert_eq(original_subarray[var].data, da.from_zarr(zarr_group[var]))
            print(var, " OK")

