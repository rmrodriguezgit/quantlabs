from runtime.profiles import detect_compute_profile

def test_profile_shape(): assert 'mode' in detect_compute_profile()
