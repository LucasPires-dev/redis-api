from apps.people.cpf import is_valid_cpf, normalize_cpf


def test_cpf_check_digits():
    assert is_valid_cpf("52998224725")
    assert is_valid_cpf(normalize_cpf("111.444.777-35"))
    assert not is_valid_cpf("11144477700")
    assert not is_valid_cpf("11111111111")  # repeated digits pass the math but aren't real
    assert not is_valid_cpf("1234")
