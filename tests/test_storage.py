from app.storage import StorageClient


def test_storage_client_exposes_expected_interface():
    methods = ["upload_input", "upload_output", "download_input",
               "signed_output_url", "delete_job"]
    for m in methods:
        assert hasattr(StorageClient, m), f"Missing method: {m}"
