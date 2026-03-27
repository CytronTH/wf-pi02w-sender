import os
import sys
import logging
import paramiko

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SFTPHandler:
    def __init__(self, sftp_config):
        self.host = sftp_config.get('host', '')
        self.port = int(sftp_config.get('port', 22))
        self.username = sftp_config.get('username', '')
        self.password = sftp_config.get('password', '')
        self.remote_path = sftp_config.get('remote_path', '/')
        self.enabled = sftp_config.get('sftp_enabled', False)
        
    def upload_files(self, file_paths):
        if not self.enabled:
            return
            
        if not file_paths:
            return

        transport = None
        sftp = None
        
        try:
            # Initialize Transport
            transport = paramiko.Transport((self.host, self.port))
            transport.connect(username=self.username, password=self.password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # Directory Verification
            current_path = ""
            for folder in self.remote_path.lstrip('/').split('/'):
                if not folder:
                    continue
                current_path += "/" + folder
                try:
                    sftp.chdir(current_path)
                except IOError:
                    logger.info(f"Directory {current_path} does not exist. Creating...")
                    try:
                        sftp.mkdir(current_path)
                        sftp.chdir(current_path)
                    except Exception as e:
                        logger.error(f"Failed to create directory {current_path}: {e}")
                        return
                        
            # We are now in self.remote_path
            logger.info(f"Connected to SFTP, target directory: {self.remote_path}. Preparing {len(file_paths)} files.")
            
            # Upload & Cleanup Sequence
            for local_path in file_paths:
                if not os.path.exists(local_path):
                    logger.warning(f"File {local_path} does not exist locally. Skipping.")
                    continue
                    
                filename = os.path.basename(local_path)
                try:
                    # Upload the file
                    sftp.put(local_path, filename)
                    logger.info(f"Successfully uploaded: {filename}")
                    
                    # Delete local file immediately to reclaim space
                    try:
                        os.remove(local_path)
                        logger.info(f"Cleaned up local file: {local_path}")
                    except OSError as e:
                        logger.error(f"Error deleting local file {local_path} after upload: {e}")
                except Exception as e:
                    logger.error(f"Failed to upload {filename}: {e}")
                    
        except Exception as e:
            logger.error(f"SFTP connection or unhandled error: {e}")
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except:
                    pass
            if transport is not None:
                try:
                    transport.close()
                except:
                    pass
