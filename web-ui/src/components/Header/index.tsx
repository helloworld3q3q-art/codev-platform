import { AvatarDropdown } from '@/components';
import { useModel } from '@umijs/max';
import { Space } from 'antd';
import React from 'react';

//const logoSrc = require('@/assets/img/icon-logo-dark.svg');
import logoSrc from '@/assets/img/icon-logo-dark.svg';

interface HeaderProps {
  logo?: React.ReactNode;
  title?: string;
  extra?: React.ReactNode;
}

const Header: React.FC<HeaderProps> = ({ logo, title = 'AI 基础服务平台', extra }) => {
  const { userInfo } = useModel('user');
  const currentUser = userInfo;
  return (
    <div className="flex justify-between items-center w-full h-50 px-24 bg-white border-b border-#f0f0f0">
      {/* 左侧Logo和标题 */}
      <div className="flex items-center gap-16">
        <div className="flex items-center">
          {logo || <img src={logoSrc} alt="logo" className="h-35 w-auto" />}
        </div>
        <div className="text-18 font-bold text-#000000d9 font-20 tracking-2">{title}</div>
      </div>

      {/* 右侧内容 */}
      <div className="flex items-center gap-16">
        {extra}
        <Space size={16}>
          {/* 用户头像和下拉菜单 */}
          <div className="cursor-pointer">
            <AvatarDropdown>
              <span className="flex items-center">
                <img
                  src={
                    currentUser?.avatar ||
                    'data:image/png;base64,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'
                  }
                  alt="avatar"
                  className="h-24 w-24 rounded-12 mr-10"
                />
                <span className="text-#0d1c2e">{currentUser.userName || 'Nika'}</span>
                <img
                  className="ml-10"
                  src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABMAAAATCAYAAAByUDbMAAABP2lDQ1BzUDMAAHicY2BgfJCTnFvMosDAkJtXUhTk7qQQERmlwP6IgZlBhIGTgY9BNjG5uMA32C2EAQiKE8uLk0uKchhQwLdrDIwg+rJuRmJeSuBGvvA5PAySFjsnd7Al7qhhwA+4UlKLk4H0HyBWSi4oKmFgYFQAsctLCkBsFyBbJDkjMQXIjgCydYqADgSyW0Di6RD2DBA7CcJeA2IXhQQ5A9kHgGyFdCR2EhI7N6c0GeoGkOt5UvNCg4E0GxDLMBQzBDAYA8MEnxpnIDRgUASFF3o4FKcZG0F08TgxMLDe+///syoDA/tkBoa/E/7//73w//+/ixgYmO8wMBzIQ+hvvs/AYLv/////uxFiXvsZGDaaA4NpJ0JMw4KBQZCLgeHEzoLEokSwEDMQM6VlMjB8Ws7AwBvJwCB8AagnGgDgnl/JCHJJ9gAAAARzQklUCAgICHwIZIgAAADPSURBVDiN7c+9CcJQFAXgc+IfSBKi4A9qkQUMjpARMoKWVo6gGziClm4gdpaWohOICtEqJBYhCs9KCDF59sHTvce9H+cC/+QnhfjDMAdGUW2sS1q79gxuu6wltWfNKnpzUq13NqHnhp9/JT4UhjAI2goxV3vWLAsiMAXoSJu9Hq5X0ZonkA4Bu6y3GPm37TcEQIiRfz7sMjEAiIL7Pg1MQsH1uEzuMu0UANC6/SHIBQAIiC1BWwalNktvSPMXJMXioCBNUoyDy3Elm/8nT3kDdK1QYcmSjTIAAAAASUVORK5CYII="
                />
              </span>
            </AvatarDropdown>
          </div>
        </Space>
      </div>
    </div>
  );
};

export default Header;
