import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import fr from './locales/fr.json';
import it from './locales/it.json';
import en from './locales/en.json';

export const SUPPORTED_LANGUAGES = [
  { code: 'fr', label: 'Francais', flag: '🇫🇷' },
  { code: 'it', label: 'Italiano', flag: '🇮🇹' },
  { code: 'en', label: 'English', flag: '🇬🇧' },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code'];

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      fr: { translation: fr },
      it: { translation: it },
      en: { translation: en },
    },
    fallbackLng: 'fr',
    supportedLngs: ['fr', 'it', 'en'],
    interpolation: { escapeValue: false }, // React already escapes
    detection: {
      // Order: localStorage (user choice) → browser language → fallback fr
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'vpp_language',
      caches: ['localStorage'],
    },
  });

export default i18n;
