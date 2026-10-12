from ibkr_etl.monthly_artifacts import _factsheet_classification


def test_video_game_publishers_are_communication_services_as_in_the_deck():
    # Yahoo, and therefore the deck's sector chart, puts Take-Two under
    # Communication Services; the factsheet must not move it to IT.
    assert _factsheet_classification(
        'TTWO', 'Electronic Gaming & Multimedia', 'Communication Services'
    ) == ('United States', 'Telecommunication Services', 'Media & Entertainment')


def test_software_stays_information_technology():
    assert _factsheet_classification(
        'MSFT', 'Software—Infrastructure', 'Technology'
    ) == ('United States', 'Information Technology', 'Software & Services')
