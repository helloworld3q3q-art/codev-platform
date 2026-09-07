import { showSuccess } from '@/components/Modal';
import { downloadStreamFileSimple } from '@/utils';
import { downloadFile, uploadFile } from '@/utils/fetch/fetch';
import { i18nMessages } from '@/utils/i18n';
import { DownloadOutlined, InboxOutlined } from '@ant-design/icons';
import { Button, message, Modal, Upload } from 'antd';
import type { UploadFile, UploadProps } from 'antd/es/upload/interface';
import React, { useState } from 'react';
import styles from './index.less';

interface UploadModalProps {
  open: boolean;
  title?: string;
  templateUrl?: string;
  uploadUrl: string;
  showSuccessModal?: boolean;
  onCancel: () => void;
  onSuccess?: (response: any) => void;
}

const { Dragger } = Upload;

const UploadModal: React.FC<UploadModalProps> = ({
  open,
  title = i18nMessages('component.uploadModal.title', '导入'),
  templateUrl,
  uploadUrl,
  showSuccessModal = true,
  onCancel,
  onSuccess,
}) => {
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);

  // 下载模板
  const handleDownloadTemplate = () => {
    if (templateUrl) {
      downloadFile({
        url: templateUrl,
      });
    }
  };
  const showSuccessModalTips = (data: any) => {
    if (showSuccessModal) {
      showSuccess({
        title: i18nMessages('importResult', '导入结果'),
        content: (
          <div>
            <p>
              {i18nMessages('totalNumbers', '总共数量')}：
              {data.totalnum || data.successnum + data.errornum || 0}{' '}
              {i18nMessages('numbers', '条')}
            </p>
            <p>
              {i18nMessages('successNumbers', '成功数量')}：{data.successnum || 0}{' '}
              {i18nMessages('numbers', '条')}
            </p>
            <p>
              {i18nMessages('failNumbers', '失败数量')}：{data.errornum || 0}{' '}
              {i18nMessages('numbers', '条')}
            </p>
            {data.errornum > 0 && (
              <p>
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    downloadStreamFileSimple(data.data, data.contentDisposition);
                  }}
                >
                  {i18nMessages('downloadErrFile', '下载错误文件')}
                </a>
              </p>
            )}
          </div>
        ),
      });
    }
  };
  // 自定义上传
  const customRequest = async (options: any) => {
    const { file, onSuccess: onUploadSuccess, onError } = options;

    setUploading(true);
    try {
      const response = await uploadFile({
        url: uploadUrl,
        file,
        responseType: 'blob',
        onProgress: (percent) => {
          const newFileList = [...fileList];
          const targetFile = newFileList.find((item) => item.uid === file.uid);
          if (targetFile) {
            targetFile.percent = percent;
            setFileList(newFileList);
          }
        },
      });
      setUploading(false);
      // message.success(i18nMessages('component.uploadModal.success', '导入成功'));
      onUploadSuccess(response, file);

      if (onSuccess) {
        onSuccess(response);
      }
      if (showSuccessModal) {
        showSuccessModalTips(response);
      }
      onCancel();
      // 清空文件列表
      setFileList([]);
    } catch (error) {
      setUploading(false);
      message.error(i18nMessages('component.uploadModal.failed', '导入失败'));
      onError(error);
    }
  };

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    fileList,
    beforeUpload: (file) => {
      // 检查文件类型，这里假设只接受 Excel 文件
      const isExcel =
        file.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' ||
        file.type === 'application/vnd.ms-excel';
      if (!isExcel) {
        message.error(i18nMessages('component.uploadModal.fileTypeError', '只能上传 Excel 文件！'));
        return Upload.LIST_IGNORE;
      }
      return false; // 返回 false 阻止自动上传
    },
    onChange: ({ fileList: newFileList }) => {
      setFileList(newFileList);
    },
    customRequest,
    onRemove: () => {
      setFileList([]);
      return true;
    },
  };
  const cancel = () => {
    setFileList([]);
    if (onCancel) {
      onCancel();
    }
  };
  return (
    <Modal
      size={700}
      title={title}
      open={open}
      onCancel={cancel}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          {i18nMessages('component.uploadModal.cancel', '取消')}
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={uploading}
          disabled={fileList.length === 0}
          onClick={() => {
            // 触发上传
            if (fileList.length > 0 && fileList[0].originFileObj) {
              customRequest({
                file: fileList[0].originFileObj,
                onSuccess: () => {},
                onError: () => {},
              });
            }
          }}
        >
          {i18nMessages('component.uploadModal.save', '保存')}
        </Button>,
      ]}
    >
      <div className={styles['import-modal-content']}>
        <div className="flex flex-between mb-20">
          <div>{i18nMessages('component.uploadModal.tip', '请按照模板格式填写并导入文件')}</div>
          {templateUrl && (
            <div className="template-download">
              <Button icon={<DownloadOutlined />} onClick={handleDownloadTemplate}>
                {i18nMessages('component.uploadModal.downloadTemplate', '下载模板')}
              </Button>
            </div>
          )}
        </div>

        <Dragger {...uploadProps}>
          <p>
            <InboxOutlined />
          </p>
          <p>{i18nMessages('component.uploadModal.dragTip', '单击或拖动文件到此区域上传')}</p>
        </Dragger>
      </div>
    </Modal>
  );
};

export default UploadModal;
