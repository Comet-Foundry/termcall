from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from main import cli


def test_preview_help_lists_flags():
    result = CliRunner().invoke(cli, ["preview", "--help"])
    assert result.exit_code == 0
    assert "--fps" in result.output
    assert "--mirror" in result.output


def test_preview_rejects_non_positive_fps():
    result = CliRunner().invoke(cli, ["preview", "--fps", "0"])
    assert result.exit_code != 0
    assert "greater than 0" in result.output


@patch("termcall.preview.cv2.VideoCapture")
def test_preview_reports_unopenable_camera(mock_video_capture):
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    mock_video_capture.return_value = mock_cap

    result = CliRunner().invoke(cli, ["preview", "--device", "3"])
    assert result.exit_code != 0
    assert "Could not open camera device 3" in result.output
