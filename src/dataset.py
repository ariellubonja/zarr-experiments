# Author: Ariel Lubonja <ariellubonja@live.com>
# Date: 26-Dec-2023


from abc import ABC, abstractmethod
import queue
import threading
from src.utils import write_utils
import xarray as xr
import dask
import glob
import os


class Dataset(ABC):
    """
    A class meant to represent a Dataset to be distributed into Johns Hopkins' FileDB.

    ...

    Attributes
    ----------
    name : str
        The name of the dataset
    location_path : str
        Path to the directory containing Xarray-compatible files to be Zarrified and distributed to FileDB. One file
         in the directory should belong to one timestep of the data
    desired_zarr_chunk_size : int
        The chunk size to be used when writing to Zarr
    desired_zarr_array_length : int
        The desired side length of the 3D data cube represented by each Zarr Group

    ...

    Methods
    -------
    _get_data_cube_side():
        Gets the side length of the 3D data cube (private method)
    transform_to_zarr():
        Transforms the dataset to Zarr format (must be implemented by subclasses)
    distribute_to_filedb(PROD_OR_BACKUP='prod', USE_DASK=False, NUM_THREADS=34):
        Distributes the dataset to FileDB using Ryan Hausen's node_assignment() node coloring alg.
    """

    def __init__(self, name, location_path, desired_zarr_chunk_size, desired_zarr_array_length, prod_or_backup,
                 start_timestep, end_timestep):
        self.name = name
        self.location_path = location_path  # List of paths
        self.desired_zarr_chunk_size = desired_zarr_chunk_size
        self.desired_zarr_array_length = desired_zarr_array_length
        self.prod_or_backup = prod_or_backup
        self.original_array_length = None  # Must be populated by subclass method
        self.start_timestep = start_timestep
        self.end_timestep = end_timestep
        # TODO add something that reads end_timestep from folder instead of having to be specified

        # TODO Generalize this. It's hard-coded for NCAR
        self.encoding = {
            "velocity": dict(chunks=(desired_zarr_chunk_size, desired_zarr_chunk_size, desired_zarr_chunk_size, 3),
                             compressor=None),
            "pressure": dict(chunks=(desired_zarr_chunk_size, desired_zarr_chunk_size, desired_zarr_chunk_size, 1),
                             compressor=None),
            "temperature": dict(chunks=(desired_zarr_chunk_size, desired_zarr_chunk_size, desired_zarr_chunk_size, 1),
                                compressor=None),
            "energy": dict(chunks=(desired_zarr_chunk_size, desired_zarr_chunk_size, desired_zarr_chunk_size, 1),
                           compressor=None)}

    def _get_data_cube_side(self, data_xarray):
        raise NotImplementedError('TODO Implement reading the length of the 3D cube side from path')

    def get_zarr_array_destinations(self, timestep: int):
        """
        Destinations of all Zarr arrays pertaining to how they are distributed on FileDB, according to Node Coloring
        Args:
            timestep (int): timestep of the dataset to return paths for

        Returns:
            list (str): List of destination paths of Zarr arrays
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def transform_to_zarr(self, file_path):
        """
        Function that converts the dataset to Zarr format. Dataset-specific and therefore must be implemented by
        subclasses
        """
        raise NotImplementedError("Subclasses must implement this method")

    def distribute_to_filedb(self, NUM_THREADS=34):
        '''
        Distribute the dataset to FileDB using Ryan Hausen's node_assignment() node coloring alg.

        Args:
            NUM_THREADS (int): Number of threads to use when writing to disk. Currently 34 to match nr. of disks on
                FileDB
        '''
        # TODO Implement backup copy write
        for timestep in range(self.start_timestep, self.end_timestep + 1):
            lazy_zarr_cubes = self.transform_to_zarr(timestep)

            q = queue.Queue()

            dests = write_utils.get_zarr_array_destinations(self)

            # Populate the queue with Write to FileDB tasks
            for i in range(len(dests)):
                q.put((lazy_zarr_cubes[i], dests[i], self.encoding))

            threads = []  # Create threads and start them
            for _ in range(NUM_THREADS):
                t = threading.Thread(target=write_utils.write_to_disk, args=(q,))
                t.start()
                threads.append(t)

            q.join()  # Wait for all tasks to be processed

            for t in threads:  # Wait for all threads to finish
                t.join()


class NCAR_Dataset(Dataset):
    """
        National Center for Atmospheric Research (NCAR) 2048^3 dataset.

        This class implements transform_to_zarr(). Please see Dataset superclass for more details
    """

    def __init__(self, name, location_path, desired_zarr_chunk_size, desired_zarr_array_length, prod_or_backup,
                 start_timestep, end_timestep):
        super().__init__(name, location_path, desired_zarr_chunk_size, desired_zarr_array_length, prod_or_backup,
                         start_timestep, end_timestep)

        self.file_extension = '.nc'
        self.NCAR_files = glob.glob(os.path.join(self.location_path, f'*{self.file_extension}'))
        self.original_array_length = 2048

    def transform_to_zarr(self, timestep: int) -> list:
        """
        Read and lazily transform the NetCDF data of NCAR to Zarr. This makes data ready for distributing to FileDB.
        """
        cubes, _ = self._prepare_NCAR_NetCDF(timestep)
        cubes = write_utils.flatten_3d_list(cubes)

        return cubes

    def _prepare_NCAR_NetCDF(self, timestep: int):
        """
        Prepare data for writing to FileDB. This includes:
            - Merging velocity components
            - Splitting into smaller chunks (64^3 or desired_zarr_chunk_size^3)
            - Unabbreviating variable names
            - Splitting 2048^3 arrays into 512^3 chunks (original_array_length -> desired_zarr_array_length)

        This function deals with the intricaties of the NCAR dataset. It is not meant to be used for other datasets.
        """
        # TODO The variable names are hard-coded

        # Open the dataset using xarray
        data_xr = xr.open_dataset(self.NCAR_files[timestep],
                                  chunks={'nnz': self.desired_zarr_chunk_size, 'nny': self.desired_zarr_chunk_size,
                                          'nnx': self.desired_zarr_chunk_size})

        assert isinstance(data_xr['e'].data, dask.array.core.Array)

        self.array_cube_side = self._get_data_cube_side(data_xr)

        # Add an extra dimension to the data to match isotropic8192 Drop is there to drop the Coordinates object
        # that is created - this creates a separate folder when to_zarr() is called
        expanded_ds = data_xr.expand_dims({'extra_dim': [1]}).drop_vars('extra_dim')
        # The above adds the extra dimension to the start. Fix that - put it in the back
        transposed_ds = expanded_ds.transpose('nnz', 'nny', 'nnx', 'extra_dim')

        # Group 3 velocity components together
        # Never use dask with remote network location on this!!
        merged_velocity = write_utils.merge_velocities(transposed_ds, chunk_size_base=self.desired_zarr_chunk_size)

        # TODO this is also hard-coded
        merged_velocity = merged_velocity.rename({'e': 'energy', 't': 'temperature', 'p': 'pressure'})

        dims = [dim for dim in data_xr.dims]
        dims.reverse()  # use (nnz, nny, nnx) instead of (nnx, nny, nnz)

        # Split 2048^3 into smaller 512^3 arrays
        smaller_groups, range_list = write_utils.split_zarr_group(merged_velocity, self.desired_zarr_array_length, dims)

        return smaller_groups, range_list

    def _get_data_cube_side(self, data_xarray: xr.Dataset) -> int:
        """
        Gets the side length of one 3D cube for the NCAR dataset (private method)

        Args:
            data_xarray (xarray.Dataset): The xarray Dataset object containing the data

        Returns:
            int: The side length of the array cube. For example, if each Variable in the dataset is 2048^3, then this
                function returns 2048
        """
        return data_xarray['e'].data.shape[0]

    def get_zarr_array_destinations(self, timestep: int):
        """
        Destinations of all Zarr arrays pertaining to how they are distributed on FileDB, according to Node Coloring
        Args:
            dataset (Dataset): dataset object filled with attributes of Dataset class
            timestep (int): timestep of the dataset to process

        Returns:
            list[str]: List of destination paths of Zarr arrays
        """
        if self.prod_or_backup == "back":
            raise NotImplementedError("TODO Implement writing backup copies")

        folders = write_utils.list_fileDB_folders()

        # TODO Hard-coded
        # Avoiding 7-2 and 9-2 - they're too full as of May 2023
        folders.remove("/home/idies/workspace/turb/data09_02/zarr/")
        folders.remove("/home/idies/workspace/turb/data07_02/zarr/")

        for i in range(len(folders)):
            folders[i] += self.name + "_" + str(i + 1).zfill(2) + "_" + self.prod_or_backup + "/"

        range_list = []  # Where chunks start and end. Needed for Mike's code to find correct chunks to access
        smaller_size = 512
        outer_dim = []

        for i in range(4):
            mid_dim = []
            for j in range(4):
                inner_dim = []

                for k in range(4):
                    a = []

                    a.append([k * smaller_size, (k + 1) * smaller_size])
                    a.append([j * smaller_size, (j + 1) * smaller_size])
                    a.append([i * smaller_size, (i + 1) * smaller_size])

                    range_list.append(a)

                mid_dim.append(inner_dim)

            outer_dim.append(mid_dim)

        chunk_morton_mapping = write_utils.get_chunk_morton_mapping(range_list, self.name)
        flattened_node_assgn = write_utils.flatten_3d_list(write_utils.node_assignment(4))

        dests = []

        for i in range(len(range_list)):
            #     for j in range(4):
            #         for k in range(4):
            min_coord = [a[0] for a in range_list[i]]
            max_coord = [a[1] - 1 for a in range_list[i]]

            morton = (write_utils.morton_pack(self.original_array_length, min_coord[2], min_coord[1], min_coord[0]),
                      write_utils.morton_pack(self.original_array_length, max_coord[2], max_coord[1], max_coord[0]))

            chunk_name = write_utils.search_dict_by_value(chunk_morton_mapping, morton)

            idx = int(chunk_name[-2:].lstrip('0'))

            filedb_index = flattened_node_assgn[idx - 1] - 1

            destination = os.path.join(folders[filedb_index],
                                       self.name + str(idx).zfill(2) + "_" + str(timestep).zfill(3) + ".zarr")

            dests.append(destination)

        return dests
