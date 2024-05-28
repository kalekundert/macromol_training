import macromol_gym_pretrain as mmgp
import macromol_gym_pretrain.dataset as _mmgp
import polars as pl
import numpy as np
import parametrize_from_file as pff

from param_helpers import make_db, image, with_np
from scipy.stats import ks_1samp
from macromol_dataframe import transform_coords, invert_coord_frame
from itertools import combinations

from hypothesis import given, example, assume
from hypothesis.strategies import floats, just
from hypothesis.extra.numpy import arrays
from pytest import approx

def test_copy_db_to_local_drive(tmp_path):
    f_src = tmp_path / 'src_db.sqlite'
    f_src.write_text('this is a database')

    f_dest = tmp_path / 'dest_db.sqlite'

    assert f_src.exists()
    assert not f_dest.exists()

    with mmgp.copy_db_to_local_drive(f_src, f_dest):
        assert f_dest.exists()
        assert f_dest.read_text() == 'this is a database'

    assert f_src.exists()
    assert not f_dest.exists()

def test_copy_db_to_local_drive_already_present(tmp_path):
    f_src = tmp_path / 'src_db.sqlite'
    f_src.write_text('this is a database')

    f_dest = tmp_path / 'dest_db.sqlite'
    f_dest.write_text('this is a database')

    assert f_src.exists()
    assert f_dest.exists()

    with mmgp.copy_db_to_local_drive(f_src, f_dest):
        assert f_dest.exists()
        assert f_dest.read_text() == 'this is a database'

    assert f_src.exists()
    assert f_dest.exists()

def test_copy_db_to_tmp(tmp_path):
    f = tmp_path / 'mock_db.sqlite'
    f.write_text('this is a database')

    with mmgp.copy_db_to_tmp(f) as tmp_f:
        assert tmp_f.exists()
        assert tmp_f.name == 'db.sqlite'
        assert tmp_f.read_text() == 'this is a database'

    assert f.exists()
    assert not tmp_f.exists()

def test_add_ligand_channel():
    atoms = pl.DataFrame([
        dict(channels=[],    is_polymer=False),
        dict(channels=[0],   is_polymer=False),
        dict(channels=[1],   is_polymer=False),
        dict(channels=[0,1], is_polymer=False),
        dict(channels=[],    is_polymer=True),
        dict(channels=[0],   is_polymer=True),
        dict(channels=[1],   is_polymer=True),
        dict(channels=[0,1], is_polymer=True),
    ])
    atoms = mmgp.add_ligand_channel(atoms, 2)

    assert atoms.to_dicts() == [
        dict(channels=[],    is_polymer=False),
        dict(channels=[0],   is_polymer=False),
        dict(channels=[1],   is_polymer=False),
        dict(channels=[0,1], is_polymer=False),
        dict(channels=[2],    is_polymer=True),
        dict(channels=[0,2],   is_polymer=True),
        dict(channels=[1,2],   is_polymer=True),
        dict(channels=[0,1,2], is_polymer=True),
    ]

@pff.parametrize(
        schema=pff.cast(
            img=image,
            mean=with_np.eval,
            std=with_np.eval,
            expected=image,
        )
)
def test_normalize_image(img, mean, std, expected):
    mmgp.normalize_image(img, mean, std)
    assert img == approx(expected)

def test_get_neighboring_frames():
    # Sample random coordinate frames, but make sure in each case that the 
    # origin of the first frame has the expected spatial relationship to the 
    # second frame.  This is just meant to catch huge errors; use the pymol 
    # plugins to evaluate the training examples more strictly.

    db, zone_ids, zone_centers_A, zone_size_A = make_db()
    db_cache = {}

    params = mmgp.NeighborParams(
            direction_candidates=mmgp.cube_faces(),
            distance_A=30,
            noise_max_distance_A=5,
            noise_max_angle_deg=10,
    )

    for i in range(100):
        zone_id, frame_ia, frame_ab, b = mmgp.get_neighboring_frames(
                db, i,
                zone_ids=zone_ids,
                neighbor_params=params,
                db_cache=db_cache,
        )
        frame_ai = invert_coord_frame(frame_ia)
        frame_ba = invert_coord_frame(frame_ab)
        frame_bi = frame_ai @ frame_ba

        # Make sure the first frame is in the correct zone.
        home_origin_i = frame_ai @ np.array([0, 0, 0, 1])
        d = home_origin_i[:3] - zone_centers_A[zone_ids.index(zone_id)]
        assert np.all(np.abs(d) <= zone_size_A)

        # Make sure the second frame is in the right position relative to the 
        # first.
        neighbor_direction_a = params.direction_candidates[b]
        neighbor_ideal_origin_a = neighbor_direction_a * params.distance_A
        neighbor_ideal_origin_i = frame_ai @ np.array([*neighbor_ideal_origin_a, 1])
        neighbor_actual_origin_i = frame_bi @ np.array([0, 0, 0, 1])
        d = neighbor_ideal_origin_i - neighbor_actual_origin_i
        assert np.linalg.norm(d[:3]) <= params.noise_max_distance_A

def test_load_from_cache():
    num_calls = 0

    def value_factory():
        nonlocal num_calls
        num_calls += 1
        return 1

    cache = {}

    assert _mmgp._load_from_cache(cache, 'a', value_factory) == 1
    assert num_calls == 1

    assert _mmgp._load_from_cache(cache, 'a', value_factory) == 1
    assert num_calls == 1

    assert _mmgp._load_from_cache(cache, 'b', value_factory) == 1
    assert num_calls == 2

    assert _mmgp._load_from_cache(cache, 'b', value_factory) == 1
    assert num_calls == 2

def test_make_neighboring_frames():
    v = lambda *x: np.array(x)

    #  Picture of the scenario being tested here:
    #
    #  y 4 │
    #      │   b │ x
    #    3 │   ──┘
    #      │   y
    #    2 │
    #      │   a │ x
    #    1 │   ──┘
    #      │   y
    #    0 └──────
    #      0  1  2
    #            x
    #
    # Frame "a" is centered at (2, 1) and frame "b" is centered at (2, 3).  
    # Both frame are rotated 90° CCW relative to the global frame.  I'm 
    # ignoring the z-direction in this test, because it's harder to reason 
    # about.

    frame_ia, frame_ab = _mmgp._make_neighboring_frames(
            home_origin_i=v(2,1,0),
            neighbor_distance=2,
            neighbor_direction_i=v(0, 1, 0),
            neighbor_direction_a=v(1, 0, 0),
    )
    frame_ib = frame_ab @ frame_ia

    assert frame_ia @ v(0, 0, 0, 1) == approx(v(-1, 2, 0, 1))
    assert frame_ib @ v(0, 0, 0, 1) == approx(v(-3, 2, 0, 1))

    assert frame_ia @ v(2, 1, 0, 1) == approx(v( 0, 0, 0, 1))
    assert frame_ib @ v(2, 1, 0, 1) == approx(v(-2, 0, 0, 1))

    assert frame_ia @ v(2, 3, 0, 1) == approx(v( 2, 0, 0, 1))
    assert frame_ib @ v(2, 3, 0, 1) == approx(v( 0, 0, 0, 1))

def test_sample_uniform_vector_in_neighborhood_2():
    # With just two possible neighbors, it's easy to check that no points end 
    # up in the wrong hemisphere.

    rng = np.random.default_rng(0)
    neighbors = np.array([
        [-1, 0, 0],
        [ 1, 0, 0],
    ])
    pairwise_rot_mat = _mmgp._precalculate_pairwise_rotation_matrices(neighbors)

    n = 1000
    x = np.zeros((2,n,3))

    for i in range(2):
        for j in range(n):
            x[i,j] = _mmgp._sample_uniform_unit_vector_in_neighborhood(
                    rng,
                    neighbors,
                    pairwise_rot_mat,
                    valid_neighbor_indices=[i],
            )

    assert np.all(x[0,:,0] <= 0)
    assert np.all(x[1,:,0] >= 0)

    # Also check that the samples are uniformly distributed, using the same 
    # KS-test as in `test_sample_uniform_unit_vector()`.

    ref = np.array([1, 0, 0])
    d = np.linalg.norm(x - ref, axis=-1).flatten()

    cdf = lambda d: d**2 / 4
    ks = ks_1samp(d, cdf)

    assert ks.pvalue > 0.05

def test_sample_uniform_vector_in_neighborhood_6():
    # With 6 possible neighbors (one for each face of the cube), it's also 
    # pretty easy to verify that the samples end up where they should.  This 
    # doesn't really test anything that the above test doesn't, but it's a bit 
    # more stringent since the neighborhoods are smaller.

    rng = np.random.default_rng(0)
    neighbors = np.array([
        [-1,  0,  0],
        [ 1,  0,  0],
        [ 0, -1,  0],
        [ 0,  1,  0],
        [ 0,  0, -1],
        [ 0,  0,  1],
    ])
    pairwise_rot_mat = _mmgp._precalculate_pairwise_rotation_matrices(neighbors)

    n = 1000
    x = np.zeros((n,3))
    
    for i in range(n):
        x[i] = _mmgp._sample_uniform_unit_vector_in_neighborhood(
                rng,
                neighbors,
                pairwise_rot_mat,
                valid_neighbor_indices=[1],
        )

    assert np.all(x[:,0] > np.abs(x[:,1]))
    assert np.all(x[:,0] > np.abs(x[:,2]))

def test_sample_uniform_unit_vector():
    # The following references give the distribution for the distance between 
    # two random points on a unit sphere of any dimension:
    #
    # https://math.stackexchange.com/questions/4654438/distribution-of-distances-between-random-points-on-spheres
    # https://johncarlosbaez.wordpress.com/2018/07/10/random-points-on-a-sphere-part-1/
    #
    # For the 3D case, the PDF is remarkably simple:
    #
    #   p(d) = d/2
    #
    # Here, we use the 1-sample KS test to check that our sampled distribution 
    # is consistent with this expected theoretical distribution.
    
    n = 1000
    rng = np.random.default_rng(0)

    d = np.zeros(n)
    x = np.array([1, 0, 0])  # arbitrary reference point

    for i in range(n):
        y = _mmgp._sample_uniform_unit_vector(rng)
        d[i] = np.linalg.norm(y - x)

    cdf = lambda d: d**2 / 4
    ks = ks_1samp(d, cdf)

    # This test should fail for 5% of random seeds, but 0 is one that passes.
    assert ks.pvalue > 0.05

def test_sample_noise_frame():
    # Don't test that the sampling is actually uniform; I think this would be 
    # hard to show, and the two underlying sampling functions are both 
    # well-tested already.  Instead, just make sure that the resulting 
    # coordinate frame doesn't distort distances.

    def calc_pairwise_distances(x):
        return np.array([
            np.linalg.norm(x[i] - x[j])
            for i, j in combinations(range(len(x)), 2)
        ])

    rng = np.random.default_rng(0)
    x = np.array([
        [1, 0, 0, 1],
        [0, 1, 0, 1],
        [0, 0, 1, 1],
    ])
    expected_dists = calc_pairwise_distances(x)

    for i in range(1000):
        frame_xy = _mmgp._sample_noise_frame(
                rng,
                max_distance_A=10,
                max_angle_deg=20,
        )
        y = transform_coords(x, frame_xy)
        actual_dists = calc_pairwise_distances(y)

        assert actual_dists == approx(expected_dists)

def test_sample_coord_from_cube():
    # Don't test that the sampling is actually uniform; I think this would be 
    # hard to do, and the implementation is simple enough that there's probably 
    # not a mistake.  Instead, just check that the points are all within the 
    # cube.

    rng = np.random.default_rng(0)

    n = 1000
    x = np.zeros((n,3))
    center = np.array([0, 2, 4])
    size = 4

    for i in range(n):
        x[i] = _mmgp._sample_coord_from_cube(rng, center, size)

    # Check that all the points are in-bounds:
    assert np.all(x[:,0] >= -2)
    assert np.all(x[:,0] <=  2)

    assert np.all(x[:,1] >  0)
    assert np.all(x[:,1] <  4)

    assert np.all(x[:,2] >  2)
    assert np.all(x[:,2] <  6)

    # Check that the sampling is uniform in each dimension:
    def cdf(a, b):
        return lambda x: (x - a) / (b - a)

    ks_x = ks_1samp(x[:,0], cdf(-2, 2))
    ks_y = ks_1samp(x[:,1], cdf( 0, 4))
    ks_z = ks_1samp(x[:,2], cdf( 2, 6))

    # Each of these tests should fail for 5% of random seeds, but 0 is one that 
    # passes.
    assert ks_x.pvalue > 0.05
    assert ks_y.pvalue > 0.05
    assert ks_z.pvalue > 0.05

def test_require_unit_vectors():
    v = np.array([[2, 0, 0], [0, 2, 0]])
    u = _mmgp._require_unit_vectors(v)
    assert u == approx(np.array([[1, 0, 0], [0, 1, 0]]))

vectors = arrays(
        dtype=float,
        shape=3,
        elements=floats(
            min_value=-1,
            max_value=1,
            allow_nan=False,
            allow_infinity=False,
        ),
        fill=just(0),
)
@given(vectors, vectors)
@example(np.array([-1, 0, 0]), np.array([1, 0, 0]))
@example(np.array([0, -1, 0]), np.array([0, 1, 0]))
@example(np.array([0, 0, -1]), np.array([0, 0, 1]))
@example(np.array([0, 0, 0]), np.array([0, 0, 1])).xfail(raises=ValueError)
def test_align_vectors(a, b):
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    assume(norm_a > 1e-6)
    assume(norm_b > 1e-6)

    R = _mmgp._align_vectors(a, b).as_matrix()

    assert R @ (b / norm_b) == approx(a / norm_a)
