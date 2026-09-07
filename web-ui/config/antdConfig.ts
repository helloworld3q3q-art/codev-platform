import themeVars from '../src/styles/theme';
import rgbToHex from './rgbToHex';

export default {
  theme: {
    token: {
      borderRadius: 6,
      colorPrimary: rgbToHex(themeVars.primary),
      colorInfo: rgbToHex(themeVars.primary),
      colorText: rgbToHex(themeVars.textPrimary),
      colorTextBase: rgbToHex(themeVars.textPrimary),
      colorTextSecondary: rgbToHex(themeVars.textSecondary),
      colorBorderSecondary: rgbToHex(themeVars.colorBorderSecondary),
    },
    components: {
      Table: {
        headerBg: rgbToHex(themeVars.tableHeaderBg),
      },
      Form: {
        labelColor: rgbToHex(themeVars.labalColor),
      },
      Button: {
        borderRadius: 6,
        colorTextLightSolid: rgbToHex(themeVars.colorTextLightSolid),
        colorBorder: rgbToHex(themeVars.colorBorder) /*  */,

        // colorPrimaryBgHover: 'red',
        // colorBgSolidHover: 'red',
        // colorBgContainerDisabled: 'red',
        //colorPrimaryHover:  'red',
        // colorPrimaryBg: 'blue',
        //colorPrimaryActive:  rgbToHex(themeVars.primaryActive)
      },
      Card: {
        borderRadius: 0,
      },
      Input: {
        borderRadius: 6,
        colorBorder: rgbToHex(themeVars.colorBorderSecondary),
      },
      Select: {
        borderRadius: 6,
        colorBorder: rgbToHex(themeVars.colorBorderSecondary),
      },
    },
  },
};
