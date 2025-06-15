"""
Refactorize experiments/reverse_ssh_tunnel.py
Make it more modular and reusable, and can load both configures from config_sender.yaml and config_receiver.yaml

"""

from typing import Optional
from core.socket_transfer_subject import SocketTransferSubject
from core.utils import build_logger
from core.progress_observer import IProgressObserver
logger = build_logger(__name__)

class ObserverContext:
    """
    Context manager for automatic observer management
    
    Ensures that observers are properly added and removed from SocketTransferSubject instances,
    and manages the observer's lifecycle (start/stop) if the observer supports it.
    This ensures proper cleanup even if exceptions occur during the transfer operations.
    """
    
    def __init__(self, 
        subject: SocketTransferSubject, 
        observer: Optional['IProgressObserver'] = None
    ):
        """
        Initialize the observer context
        
        Args:
            subject: SocketTransferSubject instance to manage
            observer: Observer instance implementing IProgressObserver interface or None
        """
        self.subject = subject
        self.observer = observer
        self._observer_added = False
        self._observer_started = False
    
    def __enter__(self):
        """Context manager entry - start observer and add to subject"""
        if self.observer:
            # Start the observer if it has a start() method
            if hasattr(self.observer, 'start') and callable(getattr(self.observer, 'start')):
                try:
                    self.observer.start()
                    self._observer_started = True
                    logger.debug(f"Observer {self.observer.__class__.__name__} started")
                except Exception as e:
                    logger.warning(f"Failed to start observer {self.observer.__class__.__name__}: {e}")
            
            # Add observer to subject
            self.subject.add_observer(self.observer)
            self._observer_added = True
            logger.debug(f"Observer {self.observer.__class__.__name__} added to transfer subject")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - remove observer from subject and stop observer"""
        if self.observer:
            # Remove observer from subject
            if self._observer_added:
                self.subject.remove_observer(self.observer)
                self._observer_added = False
                logger.debug(f"Observer {self.observer.__class__.__name__} removed from transfer subject")
            
            # Stop the observer if it was started and has a stop() method
            if self._observer_started and hasattr(self.observer, 'stop') and callable(getattr(self.observer, 'stop')):
                try:
                    self.observer.stop()
                    self._observer_started = False
                    if hasattr(self.observer, 'has_living_observers') and not self.observer.has_living_observers:
                        logger.debug(f"Observer {self.observer.__class__.__name__} stopped")
                    else:
                        logger.debug(f"Observer {self.observer.__class__.__name__} has remaining tasks, not stopping")
                except Exception as e:
                    logger.warning(f"Failed to stop observer {self.observer.__class__.__name__}: {e}")
        
        return False  # Don't suppress exceptions