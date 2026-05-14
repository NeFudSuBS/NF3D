"""
Spellchecker service: Word checking and correction.

Handles:
- Spell checking (uses pyspellchecker library)
- Word correction suggestions
- Dictionary management
- Caching for performance
"""

import logging
import threading
from typing import Optional, Set, List
from functools import lru_cache

logger = logging.getLogger(__name__)


class SpellcheckerService:
    """
    Wrapper for pyspellchecker.
    
    Provides:
    - Thread-safe spell checking
    - Word correction suggestions
    - Custom dictionary management
    - Caching for performance
    
    Degrades gracefully if spellchecker is not installed.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Implement singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self) -> None:
        """
        Initialize spellchecker.
        
        Will fail gracefully if spellchecker not installed.
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.checker = None
        self.available = False
        self._correction_cache = {}  # word.lower() → suggestion
        self.session_dictionary: Set[str] = set()
        self.persistent_dictionary: Set[str] = set()
        
        try:
            from spellchecker import SpellChecker
            self.checker = SpellChecker(language='en')
            self.available = True
            self.logger.info("✓ Spellchecker initialized")
        except ImportError:
            self.logger.warning(
                "pyspellchecker not installed. Spell checking disabled. "
                "Install with: pip install pyspellchecker"
            )
            self.available = False
        except Exception as e:
            self.logger.error(f"Failed to initialize spellchecker: {e}")
            self.available = False
    
    # ==================== Spell Checking ====================
    
    def check_word(self, word: str) -> bool:
        """
        Check if word is spelled correctly.
        
        Args:
            word: Word to check
            
        Returns:
            True if correct, False if misspelled
        """
        if not self.available or not self.checker:
            return True  # Degrade gracefully
        
        # Check user dictionaries first
        if word.lower() in self.session_dictionary:
            return True
        if word.lower() in self.persistent_dictionary:
            return True
        
        # Check with spellchecker
        return word not in self.checker.unknown([word])
    
    def get_suggestion(self, word: str) -> str:
        """
        Get spelling suggestion for a word.
        
        Uses caching to avoid expensive computation for repeated words.
        
        Args:
            word: Word to get suggestion for
            
        Returns:
            Suggested correction or empty string if no suggestion
        """
        if not self.available or not self.checker:
            return ""
        
        # Check cache
        key = word.lower()
        if key in self._correction_cache:
            return self._correction_cache[key]
        
        # Skip if in any dictionary
        if key in self.session_dictionary or key in self.persistent_dictionary:
            return ""
        
        try:
            # Get correction - this can be expensive
            suggestion = self.checker.correction(word) or ""
            
            # Cache result
            self._correction_cache[key] = suggestion
            
            if suggestion:
                self.logger.debug(f"Suggestion for '{word}': '{suggestion}'")
            
            return suggestion
            
        except Exception as e:
            self.logger.error(f"Error getting suggestion for '{word}': {e}")
            return ""
    
    def get_suggestions(self, word: str, max_suggestions: int = 5) -> List[str]:
        """
        Get multiple spelling suggestions.
        
        Args:
            word: Word to get suggestions for
            max_suggestions: Maximum number of suggestions
            
        Returns:
            List of suggestions
        """
        if not self.available or not self.checker:
            return []
        
        try:
            # Get all candidates
            candidates = self.checker.candidates(word) or []
            
            # Filter and limit
            suggestions = [
                c for c in candidates
                if c.lower() not in self.session_dictionary
                and c.lower() not in self.persistent_dictionary
            ][:max_suggestions]
            
            return suggestions
            
        except Exception as e:
            self.logger.error(f"Error getting suggestions for '{word}': {e}")
            return []
    
    # ==================== Dictionary Management ====================
    
    def add_word_session(self, word: str) -> None:
        """
        Add word to session dictionary (current session only).
        
        Args:
            word: Word to add
        """
        self.session_dictionary.add(word.lower())
        
        # Clear cached suggestion for this word
        self._correction_cache.pop(word.lower(), None)
        
        self.logger.debug(f"Added '{word}' to session dictionary")
    
    def add_words_session(self, words: List[str]) -> None:
        """
        Add multiple words to session dictionary.
        
        Args:
            words: List of words to add
        """
        for word in words:
            self.add_word_session(word)
    
    def add_word_persistent(self, word: str) -> None:
        """
        Add word to persistent dictionary.
        
        Args:
            word: Word to add
        """
        self.persistent_dictionary.add(word.lower())
        
        # Clear cached suggestion
        self._correction_cache.pop(word.lower(), None)
        
        self.logger.debug(f"Added '{word}' to persistent dictionary")
    
    def add_words_persistent(self, words: List[str]) -> None:
        """
        Add multiple words to persistent dictionary.
        
        Args:
            words: List of words to add
        """
        for word in words:
            self.add_word_persistent(word)
    
    def remove_word(self, word: str) -> None:
        """
        Remove word from all dictionaries.
        
        Args:
            word: Word to remove
        """
        self.session_dictionary.discard(word.lower())
        self.persistent_dictionary.discard(word.lower())
        self._correction_cache.pop(word.lower(), None)
        
        self.logger.debug(f"Removed '{word}' from dictionaries")
    
    def clear_session_dictionary(self) -> None:
        """
        Clear all session dictionary entries.
        """
        count = len(self.session_dictionary)
        self.session_dictionary.clear()
        self._correction_cache.clear()
        self.logger.debug(f"Cleared session dictionary ({count} words)")
    
    def get_session_dictionary(self) -> Set[str]:
        """
        Get current session dictionary.
        
        Returns:
            Set of words in session dictionary
        """
        return self.session_dictionary.copy()
    
    def get_persistent_dictionary(self) -> Set[str]:
        """
        Get persistent dictionary.
        
        Returns:
            Set of words in persistent dictionary
        """
        return self.persistent_dictionary.copy()
    
    # ==================== Batch Operations ====================
    
    def check_text(self, text: str) -> List[str]:
        """
        Get list of misspelled words in text.
        
        Args:
            text: Text to check
            
        Returns:
            List of misspelled words (unique)
        """
        if not self.available or not self.checker:
            return []
        
        try:
            # Extract words
            import re
            words = re.findall(r"\b[a-zA-Z]+(?:['''][a-zA-Z]+)*\b", text)
            
            # Check each
            misspelled = []
            for word in words:
                if not self.check_word(word):
                    misspelled.append(word)
            
            # Return unique
            return list(set(misspelled))
            
        except Exception as e:
            self.logger.error(f"Error checking text: {e}")
            return []
    
    def is_available(self) -> bool:
        """
        Check if spellchecker is available.
        
        Returns:
            True if spellchecker is initialized and available
        """
        return self.available
