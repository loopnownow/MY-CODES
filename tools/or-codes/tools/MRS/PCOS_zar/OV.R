#-------------------------packages louding-------------------------#
library(pROC);library(readxl);library(PredictABEL)
set.seed(6) # 设置随机数种子，每次运行产生相同随机数
#-------------------------data&subgroup-------------------------#
excel <- read_excel("./histogram.xlsx",sheet = "data") #函数来自(readxl)，读取excel中data工作单的数据，./是工作路径，可查看右下角
DATA <- as.matrix(excel[,-(1:2)]);Y<- as.matrix(excel[,1])
#-----------------------roc-----------------------------------
AUC<- function(x){roc(Y, x)[["auc"]]}
auc <- apply(DATA,2,AUC)
roc <- data.frame(auc[order(auc)])
#-----------------------NRI-----------------------------------
nridata <- read_excel("./NRIdata.xlsx",sheet = "nri")[,1:5]
us <- data.frame(y = nridata$v, nridata$`PCO-US`) 
mri <- data.frame(y = nridata$v, nridata$`MF-1`) 
adc <- data.frame(y = nridata$v, nridata$ADC) 
dvalue <- data.frame(y = nridata$v, nridata$D)

model1 <- glm(y~.,data = us,x = T,y = T,family = binomial('logit')) 
model2 <- glm(y~.,data = mri,x = T,y = T,family = binomial('logit')) 
model3 <- glm(y~.,data = adc,x = T,y = T,family = binomial('logit')) 
model4 <- glm(y~.,data = dvalue,x = T,y = T,family = binomial('logit')) 

usm <- model1$fitted.values 
mrim <- model2$fitted.values 
reclassification(data = us,cOutcome = 1,predrisk1 = usm,predrisk2 = mrim,cutoff = c(0,0.5,1))

adcm <- model3$fitted.values 
dm <- model4$fitted.values 
reclassification(data = dvalue,cOutcome = 1,predrisk1 = dm,predrisk2 = adcm,cutoff = c(0,0.5,1))
#-----------------------roc-----------------------------------
auc1<-roc(nridata$v,nridata$ADC,ci = T, ci.alpha = 0.9,percent = T);auc1;pROC::coords(auc1, "best",ret="all", transpose=F)
auc2<-roc(nridata$v,nridata$D,ci = T, ci.alpha = 0.9,percent = T);auc2;pROC::coords(auc2, "best",ret="all", transpose=F)
