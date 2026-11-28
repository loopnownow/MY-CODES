data<-read.csv(file.choose(),sep=',',header=T)
dataex<-read.csv(file.choose(),sep=',',header=T)
#------------------------Package--------------------#
library(glmnet);library(caret);library(pROC);library(mRMRe);library(rms);library(corrplot);library(rmda)
label.name.P<- colnames(data[ycol])
label.name.N<- paste("Non-",label.name.P,sep="")
#---------------------------------------------------#
ycol<-1
xdata<-data[19:ncol(data)]
clin<-data[8:10]
clinex<-dataex[8:10]
doc<-data[11:16]
#------------------preProcess-----------------------#
xpre<-preProcess(xdata,method=c("center","scale","corr"),cutoff=0.8)    #data preprocess#
x<-predict(xpre,xdata)
#------------------mRMRe----------------------------#
xfeature<-mRMR.data(data = x)
#---------------------LASSO-------------------------#
xlasso<-xfeature@data       #as.matrix(x)  #
ylasso<-data[,ycol]
lasso<-cv.glmnet(xlasso,ylasso,family="binomial",type.measure="auc") #deviance
plot(lasso)
lasso.pic<-glmnet(xlasso,ylasso,family="binomial")
plot(lasso.pic)
number<-which(coef(lasso.pic,s=lasso$lambda.min)!=0)-1   #choice the feature#
signature<-colnames(x[number])
print(number)
#------------------Logistic regreesion--------------#
logdata<-data.frame(data[ycol],x[number])
names(logdata)[1]<-c("y")
log<-glm(y~.,data=logdata)  #Risk to the end#
radscore<-predict(log,x[number])
#-------------------AUC------------------------------#
roc(data[,ycol],radscore)
print(signature)
#------------------Nomograme---------------------------#
nomo<-data.frame(data[ycol],radscore,clin)
names(nomo)[1]<-c("y")
dd=datadist(nomo)
options(datadist="dd")
nomomodel<-lrm(y~.,data=nomo,x=T,y=T)
nomopic<-nomogram(nomomodel, fun= function(x)1/(1+exp(-x)), lp=F, funlabel="Risk of Positive")
plot(nomopic)
nomox<-predict(nomomodel,nomo[2:ncol(nomo)])
roc(data[,ycol],nomox)
#---------------------cli nomo---------------#
#nomo<-data.frame(data[ycol],clin)
#names(nomo)[1]<-c("y")
#dd=datadist(nomo)
#options(datadist="dd")
#nomomodel<-lrm(y~.,data=nomo,x=T,y=T)
#nomopic<-nomogram(nomomodel, fun= function(x)1/(1+exp(-x)), lp=F, funlabel="Risk of Positive")
#plot(nomopic)
#nomox<-predict(nomomodel,nomo[2:ncol(nomo)])
#roc(data[,ycol],nomox)
#roc(data[,ycol],data[,12])
#---------------------external application-------------#
for(n in 2:7)
{
  print(colnames(data[n]))
  print(roc(data[,n],nomox))
}

#---------------------validation-------------------------#
#dataex<-read.csv(file.choose(),sep=',',header=T)
clinex<-dataex[8:10]
xdataex<-dataex[13:ncol(dataex)]
xvalid<-preProcess(xdataex,method=c("center","scale"))    #data preprocess#
x1<-predict(xvalid,xdataex)
#--------------
logdataex<-data.frame(dataex[ycol],x1[signature])
names(logdataex)[1]<-c("y")
logex<-glm(y~.,data=logdataex)  #Risk to the end#
radscoreex<-predict(logex,x1[signature])
#-------------
nomoex<-data.frame(dataex[ycol],radscoreex,clinex) #
names(nomoex)[1]<-c("y")
nomomodelex<-glm(y~.,data=nomoex,x=T,y=T)
nomoxex<-predict(nomomodelex,nomoex[2:ncol(nomoex)])
roc(dataex[,ycol],nomoxex)

#------------------radscore pic----------------------#
num <-1:nrow (data)
Radscore.scale<-scale(radscore, center = T, scale = T)
Rad.pic<-data.frame(data[ycol],Radscore.scale,num)
Rad.pic<-data.frame(Rad.pic[order(Rad.pic[,2], decreasing = TRUE),],data[18],num)
names(Rad.pic)[1:5]<-c("Patient","rad","oldnum","name","num")
ggplot(Rad.pic,aes(x=num,y=rad)) + geom_bar(aes(fill=factor(Patient)),stat="identity") + theme_bw() + xlab("Patients' number") + ylab("Primary corhot radscore")+ scale_fill_manual(values=c("#30A9DE", "#E53A40"),breaks = c("1", "0"), labels = c("Positiv", "Negative")) #+ geom_text(aes(label=oldnum))
#-----------------contribution----------------------#
coni<-1:(length(number)-1)
logi.coefficients<-as.numeric(round(abs(coefficients(log)[2:length(number)]),5))
logi.feature.name<-as.character (feature.name[-1]) 
contribution<-data.frame(signature,coefficients=logi.coefficients)
contribution<-data.frame(contribution[order(contribution[,2],decreasing=F),],coni)
ggplot(contribution,aes(x=coni, y=coni)) + geom_bar(aes(fill=factor(signature)),stat="identity") + theme_bw() + xlab("Rad-features") + ylab("Contribution") + coord_flip()+ theme(axis.text.x = element_blank(),axis.text.y = element_blank())+ geom_text(aes(label=coefficients))
ggplot(contribution,aes(x=coni, y=coni)) + geom_bar(aes(fill=factor(signature)),stat="identity") + theme_bw() + xlab("Rad-features") + ylab("Contribution") + coord_polar(theta = "x")+ theme(axis.text.y = element_blank(),axis.text.x = element_blank())+ theme(axis.ticks.y = element_blank(), axis.text.y = element_blank())+ geom_text(aes(label=coefficients))
#-----------------heatmap-----------------------------#
heatmap<- data.frame(data[ycol],x[number],clin)
corr = cor(heatmap)
res <- cor.mtest(heatmap, conf.level = .95)
corrplot(corr, order = "FPC", addrect = 2,method = c("color"),tl.col="black", tl.srt = 45, p.mat = res$p, insig = "label_sig", sig.level = c(.001, .01, .05), pch.cex = .9, pch.col = "white") 
#------------------DCA---------------------------------#
datadca<-data.frame(data[ycol],radscore,clin)
names(datadca)[1]<-c("y")
simple<- decision_curve (y~ radscore, data = datadca,family = binomial(link ='logit'))
complex<- decision_curve (y~radscore+Age+RH+MS, data = datadca,family = binomial(link ='logit'))
List <- list(simple,complex)
plot_decision_curve(List, curve.names=c('Radscore','Radiomics nomogram'), cost.benefit.axis =FALSE,col= c('#E53A40','#30A9DE','#5CAB7D','#090707'), confidence.intervals=FALSE, standardize = FALSE,lwd = 3,main=paste("Clinical decision curve of",label.name.P))

plot_clinical_impact(simple,population.size = 1000,cost.benefit.axis = T, n.cost.benefits= 8,col = c('#E53A40','#090707'), confidence.intervals= T,main=paste("Radscroe's clinical impact of high-risk EC"))
plot_clinical_impact(complex,population.size = 1000,cost.benefit.axis = T, n.cost.benefits= 8,col = c('#30A9DE','#090707'), confidence.intervals= T,main=paste("Radiomics nomogram's clinical impact of high-risk EC"))

for(n in 2:7)
{
  label.name.P<- colnames(data[n])
  datadca<-data.frame(data[n],radscore,clin)
  names(datadca)[1]<-c("y")
  simple<- decision_curve (y~ radscore, data = datadca,family = binomial(link ='logit'))
  complex<- decision_curve (y~radscore+Age+RH+MS, data = datadca,family = binomial(link ='logit'))
  List <- list(simple,complex)
  DCA<-plot_decision_curve(List, curve.names=c('Radscore','Radiomics nomogram'), cost.benefit.axis =FALSE,col= c('#E53A40','#30A9DE','#5CAB7D','#090707'), confidence.intervals=FALSE, standardize = FALSE,lwd = 3,main=paste("Clinical decision curve of",label.name.P))
    print(DCA)
}

